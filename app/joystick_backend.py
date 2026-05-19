"""Joystick bridge manager for the integrated Qt application."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

SEND_LOG_LIMIT = 12
RECV_LOG_LIMIT = 20


def _candidate_bridge_pythons() -> list[Path]:
    """Return candidate Python interpreters that may host the joystick bridge."""
    candidates = [
        Path(r"D:\ProgramData\miniconda3\envs\FinsRov\python.exe"),
        Path(sys.executable).resolve(),
    ]
    existing: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.exists():
            existing.append(candidate)
    return existing


def detect_bridge_python() -> Path | None:
    """Pick the preferred Python interpreter for the joystick subprocess."""
    for candidate in _candidate_bridge_pythons():
        return candidate
    return None


BRIDGE_PYTHON = detect_bridge_python()
PYGAME_AVAILABLE = BRIDGE_PYTHON is not None


@dataclass(slots=True)
class JoystickSnapshot:
    """Serializable snapshot of the latest claw joystick state."""

    tcp_connected: bool = False
    joystick_connected: bool = False
    controller_name: str = "No joystick detected"
    endpoint: str = ""
    hover_enabled: bool = False
    yaw_loop_enabled: bool = False
    left_h: float = 0.0
    left_v: float = 0.0
    right_h: float = 0.0
    right_v: float = 0.0
    planar_x: int = 0
    planar_y: int = 0
    yaw_rate_cmd: int = 0
    depth_force_cmd: int = 0
    depth_target: int = 300
    pressed_buttons: tuple[int, ...] = field(default_factory=tuple)
    gripper_direction: int = 0
    gripper_pwm: int | None = None
    gimbal_tilt_pwm: int = 1200
    gimbal_pan_pwm: int = 1550
    send_log: tuple[str, ...] = field(default_factory=tuple)
    recv_log: tuple[str, ...] = field(default_factory=tuple)
    last_error: str = ""


class JoystickWorker(QObject):
    """Launch the joystick controller in a dedicated Python subprocess."""

    snapshot_ready = Signal(object)
    status_message = Signal(str)
    finished = Signal()

    def __init__(self, host: str, port: int, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._port = port
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_snapshot = JoystickSnapshot(endpoint=self.endpoint)

    @property
    def endpoint(self) -> str:
        """Return the configured TCP endpoint."""
        return f"{self._host}:{self._port}"

    def isRunning(self) -> bool:
        """Mirror the old QThread API used by the main window."""
        process = self._process
        return process is not None and process.poll() is None

    def start(self) -> None:
        """Start the joystick subprocess."""
        if self.isRunning():
            return

        if BRIDGE_PYTHON is None:
            self._last_snapshot.last_error = "FinsRov Python runtime not found."
            self.snapshot_ready.emit(self._last_snapshot)
            self.status_message.emit("Joystick runtime is unavailable because FinsRov python.exe was not found.")
            self.finished.emit()
            return

        bridge_script = Path(__file__).resolve().with_name("joystick_bridge.py")
        env_root = BRIDGE_PYTHON.parent.parent
        env = dict(os.environ)
        path_parts = [
            str(BRIDGE_PYTHON.parent),
            str(env_root),
            str(env_root / "Library" / "bin"),
            str(env_root / "Scripts"),
            env.get("PATH", ""),
        ]
        env["PATH"] = ";".join(part for part in path_parts if part)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                [
                    str(BRIDGE_PYTHON),
                    "-u",
                    str(bridge_script),
                    "--host",
                    self._host,
                    "--port",
                    str(self._port),
                ],
                cwd=str(env_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._last_snapshot.last_error = str(exc)
            self.snapshot_ready.emit(self._last_snapshot)
            self.status_message.emit(f"Failed to start joystick bridge process: {exc}")
            self.finished.emit()
            return

        self._process = process
        self._reader_thread = threading.Thread(target=self._read_output_loop, daemon=True)
        self._reader_thread.start()
        self.status_message.emit(f"Joystick bridge started with {BRIDGE_PYTHON}.")

    def stop(self) -> None:
        """Stop the joystick subprocess."""
        process = self._process
        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.5)

        reader = self._reader_thread
        if reader is not None and reader.is_alive():
            reader.join(timeout=1.0)

        self._process = None
        self._reader_thread = None

    def _read_output_loop(self) -> None:
        """Consume the bridge output until the subprocess exits."""
        process = self._process
        if process is None or process.stdout is None:
            self.finished.emit()
            return

        try:
            for raw_line in process.stdout:
                self._handle_stdout_line(raw_line.rstrip("\r\n"))
        finally:
            return_code = process.wait()
            if return_code not in (0, -15):
                self.status_message.emit(f"Joystick bridge exited with code {return_code}.")
            with self._lock:
                self._process = None
                self._reader_thread = None
            self.finished.emit()

    def _handle_stdout_line(self, line: str) -> None:
        """Parse one line emitted by the joystick subprocess."""
        if not line:
            return

        if line.startswith("STATUS "):
            self.status_message.emit(line[7:].strip())
            return

        if line.startswith("SNAPSHOT "):
            payload = line[9:].strip()
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                self.status_message.emit(f"Joystick bridge emitted invalid JSON: {payload[:120]}")
                return

            self._last_snapshot = JoystickSnapshot(
                tcp_connected=bool(data.get("tcp_connected", False)),
                joystick_connected=bool(data.get("joystick_connected", False)),
                controller_name=str(data.get("controller_name", "No joystick detected")),
                endpoint=str(data.get("endpoint", self.endpoint)),
                hover_enabled=bool(data.get("hover_enabled", False)),
                yaw_loop_enabled=bool(data.get("yaw_loop_enabled", False)),
                left_h=float(data.get("left_h", 0.0)),
                left_v=float(data.get("left_v", 0.0)),
                right_h=float(data.get("right_h", 0.0)),
                right_v=float(data.get("right_v", 0.0)),
                planar_x=int(data.get("planar_x", 0)),
                planar_y=int(data.get("planar_y", 0)),
                yaw_rate_cmd=int(data.get("yaw_rate_cmd", 0)),
                depth_force_cmd=int(data.get("depth_force_cmd", 0)),
                depth_target=int(data.get("depth_target", 300)),
                pressed_buttons=tuple(int(item) for item in data.get("pressed_buttons", [])),
                gripper_direction=int(data.get("gripper_direction", 0)),
                gripper_pwm=(
                    None
                    if data.get("gripper_pwm") in (None, "", "null")
                    else int(data.get("gripper_pwm"))
                ),
                gimbal_tilt_pwm=int(data.get("gimbal_tilt_pwm", 1200)),
                gimbal_pan_pwm=int(data.get("gimbal_pan_pwm", 1550)),
                send_log=tuple(str(item) for item in data.get("send_log", [])[:SEND_LOG_LIMIT]),
                recv_log=tuple(str(item) for item in data.get("recv_log", [])[:RECV_LOG_LIMIT]),
                last_error=str(data.get("last_error", "")),
            )
            self.snapshot_ready.emit(self._last_snapshot)
            return

        self.status_message.emit(line)
