"""Entry point for the integrated PySide6 + GStreamer ROV console."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def _configure_windows_runtime_paths() -> None:
    """Add Conda and GStreamer runtime folders to the Windows DLL search path."""
    if os.name != "nt":
        return

    runtime_paths: list[Path] = []

    exe_path = Path(sys.executable).resolve()
    env_root = exe_path.parent.parent
    runtime_paths.extend(
        [
            exe_path.parent,
            env_root,
            env_root / "Library" / "bin",
            env_root / "Scripts",
            env_root / "Lib" / "site-packages" / "PySide6",
            env_root / "Lib" / "site-packages" / "shiboken6",
        ]
    )

    runtime_paths.extend(
        [
        Path(r"D:\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"D:\Program Files\gstreamer\1.0\msvc_x86_64\bin"),
        Path(r"C:\Program Files\gstreamer\1.0\msvc_x86_64\bin"),
        ]
    )

    added: set[str] = set()
    for path in runtime_paths:
        if not path.exists():
            continue
        normalized = str(path)
        if normalized in added:
            continue
        added.add(normalized)

        # Keep PATH updated for subprocesses and libraries that still inspect PATH directly.
        os.environ["PATH"] = normalized + os.pathsep + os.environ.get("PATH", "")

        # Python 3.8+ on Windows needs explicit DLL directories for non-system runtimes.
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(normalized)

    # GI on Windows also needs typelib lookup paths, and the Conda environment may miss
    # the core GObject typelibs even though they exist in the package cache.
    typelib_candidates = [
        env_root / "Library" / "lib" / "girepository-1.0",
        Path(r"D:\ProgramData\miniconda3\pkgs\libglib-2.86.4-h0c9aed9_1\Library\lib\girepository-1.0"),
        Path(r"D:\ProgramData\miniconda3\pkgs\libgirepository-1.86.0-h38d6f4a_0\Library\lib\girepository-1.0"),
    ]
    existing_typelib_paths = [str(path) for path in typelib_candidates if path.exists()]
    if existing_typelib_paths:
        previous = os.environ.get("GI_TYPELIB_PATH", "")
        merged = existing_typelib_paths + ([previous] if previous else [])
        os.environ["GI_TYPELIB_PATH"] = os.pathsep.join(merged)


def _build_import_error_message(exc: Exception) -> str:
    """Return a user-friendly error message when runtime dependencies are missing."""
    return (
        "The integrated ROV app could not start because a required runtime module is missing.\n\n"
        f"Details: {exc}\n\n"
        "Please make sure the rov_yolo environment has PySide6 and the Python GStreamer bindings "
        "(gi.repository / gst-python) available before starting the app. "
        "Joystick features additionally need pygame."
    )


def main() -> int:
    """Create the Qt application and show the main window."""
    _configure_windows_runtime_paths()

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setApplicationName("ROV Integrated Console")
    app.setOrganizationName("ROV")

    try:
        from app.main_window import IntegratedMainWindow
    except Exception as exc:  # pragma: no cover - fallback path for local startup issues
        QMessageBox.critical(None, "Startup Error", _build_import_error_message(exc))
        return 1

    window = IntegratedMainWindow(project_root=Path(__file__).resolve().parent)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, lambda *_args: app.quit())
    window.showMaximized()
    try:
        return app.exec()
    finally:
        window.close()

if __name__ == "__main__":
    raise SystemExit(main())
