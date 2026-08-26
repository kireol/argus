"""Platform-appropriate user directories and atomic file writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir, user_log_dir

APP_NAME = "argus-test-creator"


def user_config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.yaml"


def user_data_path() -> Path:
    return Path(user_data_dir(APP_NAME))


def user_log_path() -> Path:
    return Path(user_log_dir(APP_NAME))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file + rename (never a torn file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
