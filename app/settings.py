"""Application settings helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths


def default_save_root() -> Path:
    """Return the default capture root under the current Windows Downloads directory."""
    downloads_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if not downloads_dir:
        downloads_dir = str(Path.home() / "Downloads")
    return Path(downloads_dir) / "ROV_Capture"


@dataclass
class AppSettings:
    """All user-editable settings used by the camera application."""

    source_mode: str = "network"
    stream_address: str = "0.0.0.0"
    stream_port: int = 5600
    local_camera_index: int = 0
    joystick_host: str = "192.168.138.2"
    joystick_port: int = 5000
    capture_interval_s: float = 1.0
    save_root: str = str(default_save_root())
    reconnect_interval_ms: int = 2000


class AppSettingsStore:
    """Small wrapper around QSettings to load and save persistent configuration."""

    def __init__(self) -> None:
        self._settings = QSettings()

    def load(self) -> AppSettings:
        """Load persisted settings or return defaults for first launch."""
        defaults = AppSettings()
        return AppSettings(
            source_mode=self._settings.value("source/mode", defaults.source_mode, type=str),
            stream_address=self._settings.value("stream/address", defaults.stream_address, type=str),
            stream_port=self._settings.value("stream/port", defaults.stream_port, type=int),
            local_camera_index=self._settings.value(
                "source/local_camera_index",
                defaults.local_camera_index,
                type=int,
            ),
            joystick_host=self._settings.value("joystick/host", defaults.joystick_host, type=str),
            joystick_port=self._settings.value("joystick/port", defaults.joystick_port, type=int),
            capture_interval_s=self._settings.value(
                "capture/interval_s",
                defaults.capture_interval_s,
                type=float,
            ),
            save_root=self._settings.value("storage/save_root", defaults.save_root, type=str),
            reconnect_interval_ms=self._settings.value(
                "stream/reconnect_interval_ms",
                defaults.reconnect_interval_ms,
                type=int,
            ),
        )

    def save(self, config: AppSettings) -> None:
        """Persist the latest UI values so they are restored on next launch."""
        self._settings.setValue("source/mode", config.source_mode)
        self._settings.setValue("stream/address", config.stream_address)
        self._settings.setValue("stream/port", config.stream_port)
        self._settings.setValue("source/local_camera_index", config.local_camera_index)
        self._settings.setValue("joystick/host", config.joystick_host)
        self._settings.setValue("joystick/port", config.joystick_port)
        self._settings.setValue("capture/interval_s", config.capture_interval_s)
        self._settings.setValue("storage/save_root", config.save_root)
        self._settings.setValue("stream/reconnect_interval_ms", config.reconnect_interval_ms)
