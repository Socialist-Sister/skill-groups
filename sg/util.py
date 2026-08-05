"""General-purpose utilities: JSON IO, dirs, hashing, tree copy, PATH lookup."""

import hashlib
import json
import shutil
from pathlib import Path

from . import errors


def read_json(path):
    """Parse a UTF-8 JSON file. Any failure is wrapped in EnvError."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise errors.EnvError(f"failed to read JSON from {path}: {exc}") from exc


def write_json(path, data):
    """Serialize data as UTF-8 JSON, creating parent dirs. Any failure -> EnvError."""
    path = Path(path)
    try:
        ensure_dir(path.parent)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        raise errors.EnvError(f"failed to write JSON to {path}: {exc}") from exc


def ensure_dir(path):
    """Create a directory (and parents); return the Path. EnvError if it is a file."""
    path = Path(path)
    if path.exists() and not path.is_dir():
        raise errors.EnvError(f"{path} exists and is not a directory")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise errors.EnvError(f"failed to create directory {path}: {exc}") from exc
    return path


def sha256_id(text):
    """First 10 hex chars of the SHA-256 digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def copy_tree(src, dst):
    """Copy a directory tree recursively. Any failure -> EnvError."""
    src = Path(src)
    dst = Path(dst)
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    except Exception as exc:
        raise errors.EnvError(f"failed to copy tree {src} -> {dst}: {exc}") from exc


def which(cmd):
    """Return the full path to cmd on PATH, or None."""
    return shutil.which(cmd)
