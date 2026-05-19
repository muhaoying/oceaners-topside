"""Dedicated joystick controller process that mirrors the working claw client."""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
import traceback
from collections import deque

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

import pygame

DIRECTION_THRESHOLD = 0.1
DEPTH_THRESHOLD = 0.2
ROTATE_THRESHOLD = 0.2
FPS = 30
SNAPSHOT_INTERVAL = 0.10
CONNECT_RETRY_SECONDS = 3.0
JOYSTICK_SCAN_SECONDS = 1.0
GRIPPER_REPEAT_INTERVAL = 0.10
GIMBAL_REPEAT_INTERVAL = 0.10

GIMBAL_TILT_INDEX = 1
GIMBAL_PAN_INDEX = 2
GIMBAL_TILT_HOME = 1200
GIMBAL_PAN_HOME = 1550
GIMBAL_TILT_MIN = 1000
GIMBAL_TILT_MAX = 1700
GIMBAL_PAN_MIN = 1100
GIMBAL_PAN_MAX = 1900
GIMBAL_STEP = 20

GRIPPER_CLOSE_BUTTON = 4
GRIPPER_OPEN_BUTTON = 5
GIMBAL_TILT_HOME_BUTTON = 1
GIMBAL_PAN_HOME_BUTTON = 2
HOVER_TOGGLE_BUTTON = 7
YAW_LOOP_TOGGLE_BUTTON = 0

DEFAULT_DEPTH_TARGET = 300
MAX_SPEED = 99

FRAME_HEADER = b"\xAA\x55"
PROPELLER_ADDR = 0x02
SERVO_ADDR = 0x03
GRIPPER_ADDR = 0x05

CMD_SET_SERVO_PWM = 0x03
CMD_TOGGLE_FLOAT = 0x10
CMD_TOGGLE_YAW_LOOP = 0x20
CMD_SET_COMBINED_MOTION = 0x31
CMD_GRIPPER_SPEED = 0x00

SEND_LOG_LIMIT = 12
RECV_LOG_LIMIT = 20


def i16le(value: int) -> bytes:
    return struct.pack("<h", int(value))


def i8(value: int) -> bytes:
    clamped = int(max(-128, min(127, int(value))))
    return struct.pack("b", clamped)


def calculate_speed(axis_value: float, threshold: float) -> int:
    if -threshold < axis_value < threshold:
        return 0
    if axis_value > 0:
        return int((axis_value - threshold) * MAX_SPEED / (1 - threshold))
    return -int((-axis_value - threshold) * MAX_SPEED / (1 - threshold))


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
            crc &= 0xFFFF
    return crc


def build_frame(addr: int, cmd: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    body = bytes([addr & 0xFF, cmd & 0xFF, len(payload) & 0xFF]) + payload
    return FRAME_HEADER + body + struct.pack("<H", crc16_modbus(body))


def emit_status(message: str) -> None:
    print(f"STATUS {message}", flush=True)


def emit_snapshot(state: dict) -> None:
    print("SNAPSHOT " + json.dumps(state, ensure_ascii=True), flush=True)


class BridgeController:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.endpoint = f"{host}:{port}"

        self.sock: socket.socket | None = None
        self.recv_buffer = b""
        self.send_log: deque[str] = deque(maxlen=SEND_LOG_LIMIT)
        self.recv_log: deque[str] = deque(maxlen=RECV_LOG_LIMIT)

        self.joystick = None
        self.joystick_name = "No joystick detected"
        self.hat_state = (0, 0)
        self.hover_enabled = False
        self.yaw_loop_enabled = False
        self.target_depth = DEFAULT_DEPTH_TARGET
        self.last_error = ""

        self.left_h = 0.0
        self.left_v = 0.0
        self.right_h = 0.0
        self.right_v = 0.0
        self.planar_x = 0
        self.planar_y = 0
        self.yaw_rate_cmd = 0
        self.depth_force_cmd = 0

        self.gimbal_tilt_pwm = GIMBAL_TILT_HOME
        self.gimbal_pan_pwm = GIMBAL_PAN_HOME
        self.gripper_direction = 0
        self.latest_gripper_pwm: int | None = None
        self.pressed_buttons: tuple[int, ...] = ()
        self.previous_buttons: set[int] = set()
        self.previous_hat_state = (0, 0)

        self.last_combined_motion = (0, 0, 0, 0)
        self.last_gimbal_tick = 0.0
        self.last_gripper_tick = 0.0

    def initialize_pygame(self) -> None:
        pygame.init()
        pygame.display.init()
        flags = getattr(pygame, "HIDDEN", 0)
        try:
            pygame.display.set_mode((1, 1), flags)
        except pygame.error:
            pygame.display.set_mode((1, 1))
        pygame.display.set_caption("ROV Joystick Bridge")
        pygame.joystick.init()

    def shutdown(self) -> None:
        self.disconnect_socket()
        try:
            if self.joystick is not None:
                self.joystick.quit()
        except pygame.error:
            pass
        pygame.quit()

    def log_send(self, message: str) -> None:
        self.send_log.appendleft(f"{time.strftime('%H:%M:%S')}  {message}")

    def log_recv(self, message: str) -> None:
        self.recv_log.appendleft(f"{time.strftime('%H:%M:%S')}  {message}")
        try:
            parts = [part.strip() for part in message.split(",")]
            if len(parts) >= 4:
                self.latest_gripper_pwm = int(float(parts[-1]))
        except ValueError:
            pass

    def try_connect(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=1.5)
            sock.setblocking(False)
        except OSError as exc:
            self.last_error = str(exc)
            emit_status(f"ROV TCP connect failed: {exc}")
            return

        self.sock = sock
        self.recv_buffer = b""
        self.last_error = ""
        self.log_send(f"# Connected {self.endpoint}")
        emit_status(f"ROV TCP connected: {self.endpoint}")

    def disconnect_socket(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.recv_buffer = b""

    def scan_joystick(self) -> None:
        if self.joystick is not None and self.joystick.get_init():
            return

        try:
            pygame.joystick.quit()
            pygame.joystick.init()
        except pygame.error as exc:
            self.last_error = str(exc)
            return

        if pygame.joystick.get_count() <= 0:
            self.joystick = None
            self.joystick_name = "No joystick detected"
            return

        try:
            joystick = pygame.joystick.Joystick(0)
            joystick.init()
        except pygame.error as exc:
            self.last_error = str(exc)
            return

        self.joystick = joystick
        self.joystick_name = joystick.get_name() or "Unnamed joystick"
        emit_status(f"Joystick detected: {self.joystick_name}")

    def poll_events(self) -> None:
        try:
            pygame.event.pump()
        except pygame.error as exc:
            self.last_error = str(exc)
            return

        if self.joystick is None:
            self.pressed_buttons = ()
            self.previous_buttons = set()
            self.hat_state = (0, 0)
            self.previous_hat_state = (0, 0)
            return

        try:
            current_buttons = {
                index for index in range(self.joystick.get_numbuttons()) if self.joystick.get_button(index)
            }
            current_hat = self.joystick.get_hat(0) if self.joystick.get_numhats() > 0 else (0, 0)
        except pygame.error as exc:
            self.last_error = str(exc)
            self.joystick = None
            self.joystick_name = "Joystick disconnected"
            self.pressed_buttons = ()
            self.previous_buttons = set()
            self.hat_state = (0, 0)
            self.previous_hat_state = (0, 0)
            emit_status("Joystick disconnected.")
            return

        for button in sorted(current_buttons - self.previous_buttons):
            self.log_send(f"JOY button down: {button}")
            self.handle_button_down(button)

        for button in sorted(self.previous_buttons - current_buttons):
            self.log_send(f"JOY button up: {button}")

        self.hat_state = current_hat
        self.previous_hat_state = current_hat
        self.pressed_buttons = tuple(sorted(current_buttons))
        self.previous_buttons = current_buttons

    def handle_button_down(self, button: int) -> None:
        if button == GIMBAL_TILT_HOME_BUTTON:
            self.gimbal_tilt_pwm = GIMBAL_TILT_HOME
            self.send_servo_pwm(GIMBAL_TILT_INDEX, self.gimbal_tilt_pwm)
            return

        if button == GIMBAL_PAN_HOME_BUTTON:
            self.gimbal_pan_pwm = GIMBAL_PAN_HOME
            self.send_servo_pwm(GIMBAL_PAN_INDEX, self.gimbal_pan_pwm)
            return

        if button == HOVER_TOGGLE_BUTTON:
            next_hovering = not self.hover_enabled
            self.send_pkt(PROPELLER_ADDR, CMD_TOGGLE_FLOAT, bytes([1 if next_hovering else 0]))
            self.hover_enabled = next_hovering
            return

        if button == YAW_LOOP_TOGGLE_BUTTON:
            next_yaw_looping = not self.yaw_loop_enabled
            self.send_pkt(PROPELLER_ADDR, CMD_TOGGLE_YAW_LOOP, bytes([1 if next_yaw_looping else 0]))
            self.yaw_loop_enabled = next_yaw_looping

    def update_axes_and_buttons(self) -> None:
        if self.joystick is None:
            self.left_h = self.left_v = self.right_h = self.right_v = 0.0
            self.pressed_buttons = ()
            return

        try:
            self.left_h = self.joystick.get_axis(0)
            self.left_v = self.joystick.get_axis(1)
            self.right_h = self.joystick.get_axis(2)
            self.right_v = self.joystick.get_axis(3)
            self.pressed_buttons = tuple(
                index for index in range(self.joystick.get_numbuttons()) if self.joystick.get_button(index)
            )
        except pygame.error as exc:
            self.last_error = str(exc)
            self.joystick = None
            self.joystick_name = "Joystick disconnected"
            self.left_h = self.left_v = self.right_h = self.right_v = 0.0
            self.pressed_buttons = ()

    def update_motion(self) -> None:
        self.planar_x = calculate_speed(self.left_h, DIRECTION_THRESHOLD)
        self.planar_y = -calculate_speed(self.left_v, DIRECTION_THRESHOLD)
        self.yaw_rate_cmd = calculate_speed(self.right_h, ROTATE_THRESHOLD)
        self.depth_force_cmd = calculate_speed(self.right_v, DEPTH_THRESHOLD)

        combined_motion = (self.planar_x, self.planar_y, self.yaw_rate_cmd, self.depth_force_cmd)
        if self.hover_enabled and combined_motion != self.last_combined_motion:
            payload = (
                i8(self.planar_x)
                + i8(self.planar_y)
                + i16le(self.yaw_rate_cmd)
                + i16le(self.depth_force_cmd)
            )
            self.send_pkt(PROPELLER_ADDR, CMD_SET_COMBINED_MOTION, payload)
            self.last_combined_motion = combined_motion

    def update_gripper(self, now: float) -> None:
        close_pressed = GRIPPER_CLOSE_BUTTON in self.pressed_buttons
        open_pressed = GRIPPER_OPEN_BUTTON in self.pressed_buttons
        if close_pressed and not open_pressed:
            direction = 1
        elif open_pressed and not close_pressed:
            direction = -1
        else:
            direction = 0

        if direction != self.gripper_direction or (
            direction != 0 and now - self.last_gripper_tick >= GRIPPER_REPEAT_INTERVAL
        ):
            self.send_pkt(GRIPPER_ADDR, CMD_GRIPPER_SPEED, i8(direction))
            self.gripper_direction = direction
            self.last_gripper_tick = now

    def update_gimbal(self, now: float) -> None:
        if self.hat_state == (0, 0) or now - self.last_gimbal_tick < GIMBAL_REPEAT_INTERVAL:
            return

        hat_x, hat_y = self.hat_state
        tilt_changed = False
        pan_changed = False

        if hat_y == 1:
            self.gimbal_tilt_pwm = min(self.gimbal_tilt_pwm + GIMBAL_STEP, GIMBAL_TILT_MAX)
            tilt_changed = True
        elif hat_y == -1:
            self.gimbal_tilt_pwm = max(self.gimbal_tilt_pwm - GIMBAL_STEP, GIMBAL_TILT_MIN)
            tilt_changed = True

        if hat_x == 1:
            self.gimbal_pan_pwm = min(self.gimbal_pan_pwm + GIMBAL_STEP, GIMBAL_PAN_MAX)
            pan_changed = True
        elif hat_x == -1:
            self.gimbal_pan_pwm = max(self.gimbal_pan_pwm - GIMBAL_STEP, GIMBAL_PAN_MIN)
            pan_changed = True

        if tilt_changed:
            self.send_servo_pwm(GIMBAL_TILT_INDEX, self.gimbal_tilt_pwm)
        if pan_changed:
            self.send_servo_pwm(GIMBAL_PAN_INDEX, self.gimbal_pan_pwm)

        self.last_gimbal_tick = now

    def poll_socket(self) -> None:
        if self.sock is None:
            return

        while True:
            try:
                data = self.sock.recv(1024)
            except BlockingIOError:
                break
            except OSError as exc:
                self.last_error = str(exc)
                emit_status(f"ROV TCP receive error: {exc}")
                self.disconnect_socket()
                break

            if not data:
                emit_status("ROV TCP connection closed.")
                self.disconnect_socket()
                break

            self.recv_buffer += data
            while b"\n" in self.recv_buffer:
                line, self.recv_buffer = self.recv_buffer.split(b"\n", 1)
                text = line.decode(errors="ignore").strip("\r")
                if text:
                    self.log_recv(text)

    def send_pkt(self, addr: int, cmd: int, payload: bytes = b"") -> None:
        if self.sock is None:
            return

        frame = build_frame(addr, cmd, payload)
        try:
            self.sock.sendall(frame)
        except OSError as exc:
            self.last_error = str(exc)
            emit_status(f"ROV TCP send error: {exc}")
            self.disconnect_socket()
            return

        self.log_send(f"TX data={frame.hex(' ')}")

    def send_servo_pwm(self, index: int, pwm: int) -> None:
        payload = bytes([index & 0xFF]) + i16le(pwm)
        self.send_pkt(SERVO_ADDR, CMD_SET_SERVO_PWM, payload)

    def snapshot(self) -> dict:
        return {
            "tcp_connected": self.sock is not None,
            "joystick_connected": self.joystick is not None,
            "controller_name": self.joystick_name,
            "endpoint": self.endpoint,
            "hover_enabled": self.hover_enabled,
            "yaw_loop_enabled": self.yaw_loop_enabled,
            "left_h": self.left_h,
            "left_v": self.left_v,
            "right_h": self.right_h,
            "right_v": self.right_v,
            "planar_x": self.planar_x,
            "planar_y": self.planar_y,
            "yaw_rate_cmd": self.yaw_rate_cmd,
            "depth_force_cmd": self.depth_force_cmd,
            "depth_target": self.target_depth,
            "pressed_buttons": list(self.pressed_buttons),
            "gripper_direction": self.gripper_direction,
            "gripper_pwm": self.latest_gripper_pwm,
            "gimbal_tilt_pwm": self.gimbal_tilt_pwm,
            "gimbal_pan_pwm": self.gimbal_pan_pwm,
            "send_log": list(self.send_log),
            "recv_log": list(self.recv_log),
            "last_error": self.last_error,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    controller = BridgeController(args.host, args.port)
    controller.initialize_pygame()
    emit_status(f"Joystick bridge ready for {controller.endpoint}.")

    next_connect_attempt = 0.0
    next_joystick_scan = 0.0
    next_snapshot_emit = 0.0

    try:
        while True:
            loop_started = time.perf_counter()
            now = time.monotonic()

            if now >= next_joystick_scan:
                controller.scan_joystick()
                next_joystick_scan = now + JOYSTICK_SCAN_SECONDS

            if controller.sock is None and now >= next_connect_attempt:
                next_connect_attempt = now + CONNECT_RETRY_SECONDS
                controller.try_connect()

            controller.poll_events()
            controller.update_axes_and_buttons()
            controller.update_motion()
            controller.update_gripper(now)
            controller.update_gimbal(now)
            controller.poll_socket()
            if now >= next_snapshot_emit:
                emit_snapshot(controller.snapshot())
                next_snapshot_emit = now + SNAPSHOT_INTERVAL

            remaining = max(0.0, (1.0 / FPS) - (time.perf_counter() - loop_started))
            time.sleep(remaining)
    except KeyboardInterrupt:
        emit_status("Joystick bridge interrupted.")
    except Exception as exc:
        controller.last_error = str(exc)
        emit_status(f"Joystick bridge crashed: {exc}")
        for line in traceback.format_exc().splitlines():
            emit_status(f"TRACE {line}")
        emit_snapshot(controller.snapshot())
        return 1
    finally:
        controller.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
