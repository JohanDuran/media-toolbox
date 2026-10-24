"""Convert between Windows paths (what you and Claude pass) and container paths (/host/...).

Your user folder (HOST_HOME) is mounted at /host in every container, so
  C:\\Users\\you\\Videos\\clip.mp4  <->  /host/Videos/clip.mp4
Paths outside HOST_HOME can't be reached and are refused with a clear message.
"""

import posixpath
import re
from typing import Any

from .config import CONTAINER_HOME, HOST_HOME, PROTECTED_DIRS

_DRIVE = re.compile(r"^[A-Za-z]:/")


class PathError(ValueError):
    pass


def windows(p: str) -> str:
    return p.replace("/", "\\")


def to_container(p: str) -> str:
    raw = str(p)
    s = raw.strip().strip('"').replace("\\", "/")
    if s == CONTAINER_HOME or s.startswith(CONTAINER_HOME + "/"):
        rest = s[len(CONTAINER_HOME):]
    else:
        if s.startswith("~/") or s == "~":
            s = HOST_HOME + s[1:]
        if not _DRIVE.match(s):
            raise PathError(f"{raw!r}: pass a full Windows path (e.g. {windows(HOST_HOME)}\\Videos\\clip.mp4)")
        if not (s.lower() == HOST_HOME.lower() or s.lower().startswith(HOST_HOME.lower() + "/")):
            raise PathError(f"{raw!r} is outside {windows(HOST_HOME)}, the only folder the toolbox can reach")
        rest = s[len(HOST_HOME):]
    out = posixpath.normpath(CONTAINER_HOME + rest)
    if not (out == CONTAINER_HOME or out.startswith(CONTAINER_HOME + "/")):
        raise PathError(f"{raw!r} escapes {windows(HOST_HOME)}")
    return out


def to_windows(c: str) -> str:
    if c == CONTAINER_HOME or c.startswith(CONTAINER_HOME + "/"):
        return windows(HOST_HOME + c[len(CONTAINER_HOME):])
    return c


def protected_dir(container_path: str) -> str | None:
    low = container_path.lower()
    for d in PROTECTED_DIRS:
        cd = to_container(d).lower()
        if low == cd or low.startswith(cd + "/"):
            return d
    return None


def check_writable(container_path: str) -> str:
    d = protected_dir(container_path)
    if d:
        raise PathError(f"{to_windows(container_path)} is inside {windows(d)}, which the toolbox never writes to")
    return container_path


def looks_like_path(s: str) -> bool:
    t = s.strip().strip('"').replace("\\", "/")
    return bool(_DRIVE.match(t)) or t.startswith(CONTAINER_HOME + "/") or t.startswith("~/")


def results_to_windows(obj: Any) -> Any:
    """Turn every /host/... string in a worker result back into a Windows path."""
    if isinstance(obj, str):
        return to_windows(obj) if obj.startswith(CONTAINER_HOME + "/") else obj
    if isinstance(obj, list):
        return [results_to_windows(v) for v in obj]
    if isinstance(obj, dict):
        return {k: results_to_windows(v) for k, v in obj.items()}
    return obj
