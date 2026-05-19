"""Storage and path management utilities."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path


class StorageManager:
    """Create capture directories and validate whether they are writable."""

    PHOTO_DIR_NAME = "photos"
    VIDEO_DIR_NAME = "videos"

    def ensure_root_writable(self, root: str | Path) -> tuple[bool, str]:
        """Return whether the selected root path can be created and written to."""
        root_path = Path(root).expanduser()
        try:
            root_path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=root_path, prefix="write_check_", delete=True):
                pass
        except Exception as exc:
            return False, f"Save directory is not writable: {root_path}\n{exc}"
        return True, ""

    def ensure_capture_dirs(self, root: str | Path) -> tuple[Path, Path]:
        """Create and return the photo/video child directories."""
        root_path = Path(root).expanduser()
        photo_dir = root_path / self.PHOTO_DIR_NAME
        video_dir = root_path / self.VIDEO_DIR_NAME
        photo_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)
        return photo_dir, video_dir

    def build_photo_path(self, root: str | Path) -> Path:
        """Return a timestamp-based JPG output path."""
        photo_dir, _ = self.ensure_capture_dirs(root)
        # Include milliseconds so fast auto-capture intervals do not overwrite files.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return photo_dir / f"photo_{ts}.jpg"

    def build_video_path(self, root: str | Path) -> Path:
        """Return a timestamp-based MP4 output path."""
        _, video_dir = self.ensure_capture_dirs(root)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return video_dir / f"video_{ts}.mp4"
