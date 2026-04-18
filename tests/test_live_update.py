import os
import sys
from pathlib import Path

# Ensure companion/ is on the path so we can import its modules
COMPANION_DIR = os.path.join(os.path.dirname(__file__), '..', 'companion')
sys.path.insert(0, os.path.abspath(COMPANION_DIR))

from live_update import DebounceGate, parse_trigger_image_path, resolve_watch_image


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


def test_parse_trigger_image_path_uses_last_non_empty_line():
    text = '\n1700000000\n\n/tmp/one.jpg\n/tmp/two.jpg\n'
    assert parse_trigger_image_path(text) == '/tmp/two.jpg'


def test_parse_trigger_image_path_handles_empty_content():
    assert parse_trigger_image_path('') is None
    assert parse_trigger_image_path('\n  \n') is None


def test_resolve_watch_image_file_mode(tmp_path: Path):
    img = tmp_path / 'image.jpg'
    img.write_bytes(b'x')
    assert resolve_watch_image(img) == img


def test_resolve_watch_image_directory_picks_latest_image(tmp_path: Path):
    older = tmp_path / 'older.jpg'
    newer = tmp_path / 'newer.png'
    other = tmp_path / 'notes.txt'
    older.write_bytes(b'1')
    newer.write_bytes(b'2')
    other.write_text('nope')

    # Ensure deterministic mtime ordering.
    older.touch()
    newer.touch()

    assert resolve_watch_image(tmp_path) == newer


def test_debounce_gate_debounces_and_throttles():
    clock = FakeClock()
    gate = DebounceGate(
        debounce_seconds=0.3,
        min_interval_seconds=1.0,
        time_fn=clock.now,
    )

    assert gate.update(('a', 1)) is False
    clock.advance(0.2)
    assert gate.update(('a', 1)) is False
    clock.advance(0.2)
    assert gate.update(('a', 1)) is True

    # New marker appears quickly: must debounce first and respect min interval.
    clock.advance(0.1)
    assert gate.update(('b', 1)) is False
    clock.advance(0.4)
    assert gate.update(('b', 1)) is False  # throttled
    clock.advance(0.6)
    assert gate.update(('b', 1)) is True


def test_debounce_gate_restarts_pending_when_marker_changes_repeatedly():
    clock = FakeClock()
    gate = DebounceGate(
        debounce_seconds=0.25,
        min_interval_seconds=0.0,
        time_fn=clock.now,
    )

    assert gate.update('m1') is False
    clock.advance(0.1)
    assert gate.update('m2') is False  # change resets pending timer
    clock.advance(0.2)
    assert gate.update('m2') is False
    clock.advance(0.1)
    assert gate.update('m2') is True
