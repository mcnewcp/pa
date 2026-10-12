"""What run-evals.sh forwards into the VM's eval run: the librarian's settings from the devbox
environment (`PA_LIBRARIAN_*`), so a run can extract with another model, and nothing else."""

from __future__ import annotations

import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib.sh"


def forwarded(environment: dict[str, str]) -> list[str]:
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}" && librarian_env'],
        capture_output=True,
        text=True,
        env={"HOME": "/nonexistent", "PATH": "/usr/bin:/bin", **environment},
        check=True,
    )
    return result.stdout.splitlines()


def test_the_librarian_settings_are_forwarded_and_nothing_else():
    assert forwarded(
        {
            "PA_LIBRARIAN_EXTRACTION_MODEL": "claude-opus-5-5",
            "PA_LIBRARIAN_EXTRACTION_EFFORT": "high",
            "PA_LIBRARIAN_MODEL": "claude-sonnet-5-5",
            "PA_OWNER_NAME": "Argus McNevans",
            "ANTHROPIC_API_KEY": "sk-not-forwarded",
        }
    ) == [
        "PA_LIBRARIAN_EXTRACTION_EFFORT=high",
        "PA_LIBRARIAN_EXTRACTION_MODEL=claude-opus-5-5",
        "PA_LIBRARIAN_MODEL=claude-sonnet-5-5",
    ]


def test_with_no_librarian_settings_nothing_is_forwarded():
    assert forwarded({"PA_OWNER_NAME": "Argus McNevans"}) == []
