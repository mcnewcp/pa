"""The vault collector: the owner's finished daily notes from the vault copy (ADR-0002).

The vault copy is a directory the headless Sync client keeps; the collector only reads it. A
daily note is captured once its day has ended in the owner's time zone, and again whenever its
bytes change, so a correction to an old note lands as its new version. Today's note is never
captured.
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
from pa_home.collectors import CollectionError, SourceInbox, read_state, write_state

_STATE = "notes.json"
# Daily notes are named for their day, which may sit among other words (`2026-10-05 Monday`).
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class VaultState(BaseModel):
    captured: dict[str, str] = {}
    """Each captured note's vault path, and the SHA-256 of the bytes last captured for it."""


class VaultCollector:
    """Collects the daily notes in `daily_notes`, a folder of the vault copy at `vault`."""

    source = Source.OBSIDIAN

    def __init__(self, vault: Path, daily_notes: str, time_zone: ZoneInfo) -> None:
        self.vault = vault
        self.daily_notes = PurePosixPath(daily_notes)
        self.time_zone = time_zone

    def collect(self, inbox: SourceInbox, state: Path, *, now: Callable[[], datetime]) -> None:
        folder = self.vault / self.daily_notes
        if not folder.is_dir():
            raise CollectionError("the vault copy's daily-notes folder doesn't exist")
        saved = read_state(state / _STATE, VaultState)
        captured = dict(saved.captured)
        today = now().astimezone(self.time_zone).date()
        for note in sorted(folder.rglob("*.md")):
            vault_path = note.relative_to(self.vault).as_posix()
            day = _day(note)
            # A link could point outside the vault; a note is a file the Sync client wrote.
            skip = note.is_symlink() or not note.is_file() or _hidden(note.relative_to(folder))
            if skip or day is None or day >= today:
                continue
            text = note.read_bytes()
            digest = hashlib.sha256(text).hexdigest()
            if captured.get(vault_path) == digest:
                continue
            read_at = now().astimezone(self.time_zone).isoformat(timespec="seconds")
            header = f"Vault-Path: {vault_path}\nCaptured-At: {read_at}\n\n".encode()
            inbox.write(day.isoformat(), header + text)
            captured[vault_path] = digest
        write_state(state / _STATE, VaultState(captured=captured))


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
