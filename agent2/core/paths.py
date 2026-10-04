"""Application data paths and best-effort private filesystem permissions."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

APP_DIRECTORY = "Agent2"


def user_data_dir() -> Path:
    """Return and create the per-user data directory for Agent2."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    directory = base / APP_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    make_private(directory, is_dir=True)
    return directory


def make_private(path: Path, *, is_dir: bool = False) -> None:
    """Restrict a file/directory to the current user where POSIX permissions apply."""
    if os.name == "nt":
        return
    try:
        path.chmod(stat.S_IRWXU if is_dir else stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Some mounted/user-managed filesystems do not allow chmod; the app can still
        # operate, but the caller must not mistake this for OS-enforced secrecy.
        pass


def database_path() -> Path:
    return user_data_dir() / "agent2.sqlite3"


def config_path() -> Path:
    return user_data_dir() / "settings.json"
