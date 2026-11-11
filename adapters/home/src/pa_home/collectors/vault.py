"""The vault collector: the owner's finished daily notes from the vault copy (ADR-0002).

The vault copy is a directory the headless Sync client keeps; the collector only reads it. A
daily note is captured once its day has ended in the owner's time zone, and again whenever its
bytes change, so a correction to an old note lands as its new version. Today's note is never
captured. After a backfill, notes for days before its window are left alone.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from pa_core.envelope import Source
from pa_core.normalizers.capture_header import CaptureHeaderError, encode_capture_header
from pa_home.collectors import CollectionError, SourceInbox, read_state, write_state

_STATE = "notes.json"
# Daily notes are named for their day, which may sit among other words (`2026-10-05 Monday`).
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class VaultState(BaseModel):
    captured: dict[str, str] = {}
    """Each captured note's vault path, and the SHA-256 of the bytes last captured for it."""
    first_day: date | None = None
    """The first day of the backfill's window: notes for earlier days are left alone."""


class VaultCollector:
    """Collects the daily notes in `daily_notes`, a folder of the vault copy at `vault`."""

    source = Source.OBSIDIAN

    def __init__(self, vault: Path, daily_notes: str, time_zone: ZoneInfo) -> None:
        self.vault = vault
        self.daily_notes = PurePosixPath(daily_notes)
        self.time_zone = time_zone

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        self._capture(inbox, state, read_state(state / _STATE, VaultState), now=now)

    def backfill(
        self,
        inbox: SourceInbox,
        state: Path,
        *,
        window_start: datetime,
        now: Callable[[], datetime],
    ) -> None:
        saved = read_state(state / _STATE, VaultState)
        first_day = window_start.astimezone(self.time_zone).date()
        self._capture(inbox, state, saved.model_copy(update={"first_day": first_day}), now=now)

    def _capture(
        self, inbox: SourceInbox, state: Path, saved: VaultState, *, now: Callable[[], datetime]
    ) -> None:
        folder = self.vault / self.daily_notes
        if not folder.is_dir():
            raise CollectionError("the vault copy's daily-notes folder doesn't exist")
        captured = dict(saved.captured)
        today = now().astimezone(self.time_zone).date()
        first_day = saved.first_day or date.min
        unwritable = 0
        for note in sorted(folder.rglob("*.md")):
            vault_path = note.relative_to(self.vault).as_posix()
            day = _day(note)
            # A link could point outside the vault; a note is a file the Sync client wrote.
            skip = note.is_symlink() or not note.is_file() or _hidden(note.relative_to(folder))
            if skip or day is None or not first_day <= day < today:
                continue
            text = note.read_bytes()
            digest = hashlib.sha256(text).hexdigest()
            if captured.get(vault_path) == digest:
                continue
            read_at = now().astimezone(self.time_zone).isoformat(timespec="seconds")
            try:
                header = encode_capture_header({"Vault-Path": vault_path, "Captured-At": read_at})
            except CaptureHeaderError:
                unwritable += 1
                continue
            inbox.write(day.isoformat(), header + text)
            captured[vault_path] = digest
        write_state(state / _STATE, saved.model_copy(update={"captured": captured}))
        if unwritable == 1:
            raise CollectionError(
                "a daily note's path holds a line break or control character; rename the note"
            )
        if unwritable:
            raise CollectionError(
                f"{unwritable} daily notes' paths hold a line break or control character; "
                "rename the notes"
            )


def _day(note: Path) -> date | None:
    """The day a daily note is for, from its name; None for a note that isn't named for one."""
    found = _ISO_DATE.search(note.stem)
    if found is None:
        return None
    try:
        return date.fromisoformat(found[0])
    except ValueError:
        return None


def _hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)
