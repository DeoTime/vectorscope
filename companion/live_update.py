"""Helpers for live update/watch behavior used by the companion app."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp', '.webp'}


def parse_trigger_image_path(text: str) -> str | None:
    """Return the image path from trigger file contents."""
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1]


def file_marker(path: Path) -> tuple[int, int] | None:
    """Return (mtime_ns, size) for change detection, or None when unavailable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def resolve_watch_image(path: Path) -> Path | None:
    """Resolve the current image to watch from a file or directory target."""
    if path.is_file():
        return path if path.suffix.lower() in IMAGE_EXTENSIONS else None

    if not path.is_dir():
        return None

    newest = None
    newest_mtime = None
    for candidate in path.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            mtime = candidate.stat().st_mtime_ns
        except OSError:
            continue
        if newest is None or mtime > newest_mtime:
            newest = candidate
            newest_mtime = mtime

    if newest is None:
        return None

    return newest


class DebounceGate:
    """Debounce + throttle gate for repeated change events."""

    def __init__(
        self,
        debounce_seconds: float,
        min_interval_seconds: float,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self.debounce_seconds = debounce_seconds
        self.min_interval_seconds = min_interval_seconds
        self._time_fn = time_fn or time.monotonic
        self._last_marker = None
        self._pending_since = None
        self._last_fire = None

    def update(self, marker) -> bool:
        """Update with the latest marker; returns True when action should fire."""
        now = self._time_fn()

        if marker is None:
            self._pending_since = None
            self._last_marker = None
            return False

        if marker != self._last_marker:
            self._last_marker = marker
            self._pending_since = now
            return False

        if self._pending_since is None:
            return False

        if (now - self._pending_since) < self.debounce_seconds:
            return False

        if self._last_fire is not None and (now - self._last_fire) < self.min_interval_seconds:
            return False

        self._last_fire = now
        self._pending_since = None
        return True
