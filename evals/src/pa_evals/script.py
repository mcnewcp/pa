"""What the scripted agent and judge play back: one entry per question id, or one per run."""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Mapping


class Script[T]:
    """Scripted entries by question id, each one entry or a list of one per run.

    A list's entries are played in order, one per call for that question id, so a run of N
    repeats can see a different entry each time. Safe to call from several threads.
    """

    def __init__(self, entries: Mapping[str, T | list[T]]) -> None:
        self._entries = dict(entries)
        self._calls: Counter[str] = Counter()
        self._lock = threading.Lock()

    def next(self, question_id: str) -> T | None:
        """The entry for this call on `question_id`, or None if none is scripted."""
        with self._lock:
            call = self._calls[question_id]
            self._calls[question_id] += 1
        entry = self._entries.get(question_id)
        if isinstance(entry, list):
            return entry[call] if call < len(entry) else None
        return entry
