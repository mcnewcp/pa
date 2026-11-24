"""One thing wrong with what a diagnostician's session left (ADR-0010).

Whoever holds a session's output to something says what is wrong with it in the same two
fields: `pa_evals.diagnosis` its shape, `pa_evals.reproduction` a reproduction's, and
`pa_evals.release_check` every rule's. So a problem is handed from one to the next as it is.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    """One thing wrong. Private: both fields can quote what the session wrote, so a problem goes
    to the session that left it, or to the owner's notes, and never to what a command prints."""

    where: str
    """Which file, and where in it: `reproductions/f1/payloads/gmail/order.eml:9`."""
    what: str

    def under(self, directory: str) -> Problem:
        """The same problem, with its place given from the directory that holds `directory`."""
        return Problem(f"{directory}/{self.where}", self.what)
