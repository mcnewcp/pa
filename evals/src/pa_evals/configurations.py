"""The configurations an eval run can ask: what the assistant is given besides L0's files.

Files alone is the only one so far. A run records its configuration's name, which keys its
results when `pa-eval compare` sets configurations side by side.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Configuration:
    name: str
    about: str
    """What it gives the assistant, for `pa-eval run --help`."""


FILES_ALONE = Configuration(
    "files-alone", "file access to L0 and no MCP server (the v0.11 configuration)"
)

CONFIGURATIONS: dict[str, Configuration] = {
    configuration.name: configuration for configuration in (FILES_ALONE,)
}
"""The configurations a run can ask, by name."""
DEFAULT_CONFIGURATION = FILES_ALONE
