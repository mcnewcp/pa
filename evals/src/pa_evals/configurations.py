"""The configurations an eval run can ask: what the assistant is given besides L0's files.

Files alone gives the assistant L0's files and nothing else. Files plus wiki gives it an eval
wiki besides (`pa_evals.eval_wiki`), laid beside the throwaway L0 read-only. A run records its
configuration's name, which keys its results when `pa-eval compare` sets configurations side by
side.
"""

from __future__ import annotations

from dataclasses import dataclass

from pa_core.errors import PaError


class WikiChoiceError(PaError):
    """A configuration with a wiki asked without an eval wiki, or one without given one."""


@dataclass(frozen=True)
class Configuration:
    name: str
    about: str
    """What it gives the assistant, for `pa-eval run --help`."""
    wiki: bool = False
    """Whether it gives the assistant a wiki, so a run of it needs an eval wiki."""

    def require_wiki_choice(self, *, wiki_given: bool) -> None:
        """Refuses a run of this configuration without an eval wiki if it gives the assistant
        one, or with one if it doesn't."""
        if self.wiki and not wiki_given:
            raise WikiChoiceError(f"{self.name} needs an eval wiki (--wiki DIR)")
        if wiki_given and not self.wiki:
            raise WikiChoiceError(f"{self.name} gives the assistant no wiki: drop --wiki")


FILES_ALONE = Configuration(
    "files-alone", "file access to L0 and no MCP server (the v0.11 configuration)"
)

FILES_PLUS_WIKI = Configuration(
    "files-plus-wiki",
    "files alone and a read-only eval wiki over the same L0 (needs --wiki DIR)",
    wiki=True,
)

CONFIGURATIONS: dict[str, Configuration] = {
    configuration.name: configuration for configuration in (FILES_ALONE, FILES_PLUS_WIKI)
}
"""The configurations a run can ask, by name."""
DEFAULT_CONFIGURATION = FILES_ALONE
