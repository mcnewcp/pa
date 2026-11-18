"""The real-data verdict (#100): files plus wiki against files alone, over the same cut.

It comes in two parts. The public verdict, committed to the repository, holds numbers only:
correctness by category with its spread, evidence recall, citation validity, cost, the search
decision and the prototype's outcome. Nothing from the real set reaches it: no question,
expected answer, answer, reasoning, or question or episode id, and categories are the eval
set's fixed names. The private verdict, kept under the data root, holds the failure analysis:
each failed answer, with what its session never opened.

**Search** (a search engine for v0.13.1) is needed only when, in some configuration, both
hold: evidence recall on the real set is lower than on the scale corpus by more than the spread
of either, and most of the evidence the answers missed never reached their sessions, so it was
never found, rather than found and left uncited.

**The prototype's eval part** is met when files plus wiki scores at least as well overall as
files alone, as the mean over their runs. The phone demo, its other part, is the owner's to
confirm.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pa_core.errors import PaError
from pa_evals.comparison import OVERALL, Spread, correctness, counts
from pa_evals.results import QuestionResult, RunResults, median

FILES_ALONE = "files-alone"
FILES_PLUS_WIKI = "files-plus-wiki"
CONFIGURATIONS = (FILES_ALONE, FILES_PLUS_WIKI)
NEVER_FOUND_SHARE = 0.5
"""More than this share of the evidence missed never reached the sessions: it was never found."""


class VerdictError(PaError):
    """Run directories that don't make a verdict."""


@dataclass(frozen=True)
class Search:
    """One configuration's evidence recall on the real set against the scale corpus's, and
    what became of the evidence its answers missed."""

    real: Spread | None
    scale: Spread | None
    missed: int
    """Evidence episodes left uncited, over every answer whose tool calls were recorded."""
    never_found: int
    """Of those, the ones whose contents never reached the session."""

    @property
    def lower(self) -> bool:
        """Whether real recall is lower than the scale corpus's by more than either spread."""
        if self.real is None or self.scale is None:
            return False
        return self.real.mean < self.scale.mean and counts(self.scale, self.real)

    @property
    def never_found_share(self) -> float | None:
        return self.never_found / self.missed if self.missed else None

    @property
    def needed(self) -> bool:
        share = self.never_found_share
        return self.lower and share is not None and share > NEVER_FOUND_SHARE


def check_runs(name: str, configuration: str, runs: Sequence[RunResults]) -> None:
    """Refuse runs that aren't all of `configuration` (given by the option `name`), or that
    weren't asked over a cut."""
    others = sorted({run.configuration or "unnamed" for run in runs} - {configuration})
    if others:
        raise VerdictError(f"{name} holds runs of {', '.join(others)}, not {configuration}")
    if any(run.cut is None for run in runs):
        raise VerdictError(f"{name} holds a run that wasn't asked over a cut")


def check_same_cut(runs: Sequence[RunResults]) -> None:
    names = {run.cut.name for run in runs if run.cut is not None}
    if len(names) != 1:
        raise VerdictError(
            f"the runs weren't all asked over the same cut ({', '.join(sorted(names))})"
        )


def spread(runs: Sequence[RunResults], score: str) -> Spread | None:
    """An overall score's mean and spread over the runs that have it."""
    values = [getattr(run.overall, score) for run in runs]
    present = [value for value in values if value is not None]
    return Spread.over(present) if present else None


def search(real: Sequence[RunResults], scale: Sequence[RunResults]) -> Search:
    missed = never_found = 0
    for q in _answers(real):
        if q.evidence_opened is None:
            continue
        uncited = [e for e in q.evidence if e not in q.cited]
        missed += len(uncited)
        never_found += sum(e not in q.evidence_opened for e in uncited)
    return Search(
        spread(real, "evidence_recall"), spread(scale, "evidence_recall"), missed, never_found
    )


def prototype_met(files_alone: Sequence[RunResults], files_plus_wiki: Sequence[RunResults]) -> bool:
    alone = correctness(files_alone, OVERALL)
    wiki = correctness(files_plus_wiki, OVERALL)
    assert alone is not None and wiki is not None
    return wiki.mean >= alone.mean - 1e-9


def render_public(
    files_alone: Sequence[RunResults],
    files_plus_wiki: Sequence[RunResults],
    scale_files_alone: Sequence[RunResults],
    scale_files_plus_wiki: Sequence[RunResults],
) -> str:
    """The public verdict: numbers only."""
    configurations = {FILES_ALONE: files_alone, FILES_PLUS_WIKI: files_plus_wiki}
    first = files_alone[0]
    assert first.cut is not None
    cut = first.cut
    asked = {c: sum(q.category == c for q in first.questions) for c in first.categories}
    versions = sorted({run.wiki.librarian_version for run in files_plus_wiki if run.wiki})
    setup = ", ".join(f"{key} {value}" for key, value in sorted(first.setup.items()))
    lines = [
        "# v0.13 verdict: the wiki on real data",
        "",
        "Numbers only. The questions, answers and failure analysis are in the private verdict "
        "under the data root.",
        "",
        f"- **The cut:** taken {cut.as_of.strftime('%Y-%m-%d %H:%M UTC')}, {cut.episodes} "
        f"episodes, the live wiki at {cut.wiki_commit or 'none'}.",
        f"- **The set:** {len(first.questions)} questions, asked at the cut's time.",
        f"- **The runs:** files alone {len(files_alone)}, files plus wiki "
        f"{len(files_plus_wiki)}"
        + (f" (librarian version {', '.join(versions)})" if versions else "")
        + (f"; {setup}" if setup else "")
        + ".",
        "",
        "## Correctness by category",
        "",
        "Mean over runs, with the spread from the lowest run to the highest. A difference counts "
        "only when it is larger than the spread of either side.",
        "",
        "| Category (questions) | Files alone | Files plus wiki | Wiki vs files alone |",
        "|---|---|---|---|",
    ]
    for category in [*asked, OVERALL]:
        alone = correctness(files_alone, category)
        wiki = correctness(files_plus_wiki, category)
        count = len(first.questions) if category == OVERALL else asked[category]
        lines.append(
            f"| {category} ({count}) | {_spread(alone)} | {_spread(wiki)} | "
            f"{_difference(alone, wiki)} |"
        )

    lines += [
        "",
        "## Evidence, citations and cost",
        "",
        "| | Files alone | Files plus wiki |",
        "|---|---|---|",
    ]
    for label, score in (
        ("Evidence recall", "evidence_recall"),
        ("Citation validity", "citation_validity"),
    ):
        cells = [_spread(spread(runs, score)) for runs in configurations.values()]
        lines.append(f"| {label} | {' | '.join(cells)} |")
    rows = {
        "Cited episodes opened": lambda runs: _score(_cited_opened(_answers(runs))),
        "Total cost": lambda runs: _dollars(_total_cost(_answers(runs))),
        "Per answer, median cost": lambda runs: _dollars(
            median([q.cost_usd for q in _answers(runs)]), places=3
        ),
        "Per answer, median turns": lambda runs: _number(median([q.turns for q in _answers(runs)])),
        "Failed answers (agent or judge)": lambda runs: str(
            sum(q.status == "failed" for q in _answers(runs))
        ),
    }
    for label, cell in rows.items():
        lines.append(f"| {label} | {' | '.join(cell(runs) for runs in configurations.values())} |")

    searches = {
        FILES_ALONE: search(files_alone, scale_files_alone),
        FILES_PLUS_WIKI: search(files_plus_wiki, scale_files_plus_wiki),
    }
    lines += [
        "",
        "## Search",
        "",
        "Evidence recall on the real set against the scale corpus's (v0.12.1), in the same "
        "configuration, and of the evidence the answers left uncited, how much never reached "
        "their sessions.",
        "",
        "| | Real set | Scale corpus | Lower by more than the spread | Missed evidence never "
        "opened |",
        "|---|---|---|---|---|",
    ]
    for name, found in searches.items():
        never = (
            "n/a (none missed)"
            if found.never_found_share is None
            else f"{found.never_found} of {found.missed} ({found.never_found_share:.2f})"
        )
        lines.append(
            f"| {_label(name)} | {_spread(found.real)} | {_spread(found.scale)} | "
            f"{'yes' if found.lower else 'no'} | {never} |"
        )
    needed = [name for name, found in searches.items() if found.needed]
    lines.append("")
    if needed:
        lines.append(
            f"**Needed:** in {', '.join(_label(n).lower() for n in needed)}, recall is lower than "
            "the scale corpus's by more than the spread, and most of the missed evidence was "
            "never opened. Plan v0.13.1's search engine."
        )
    else:
        lines.append(
            "**Not needed:** in neither configuration is recall lower than the scale corpus's "
            "by more than the spread while most of the missed evidence was never opened."
        )

    alone, wiki = correctness(files_alone, OVERALL), correctness(files_plus_wiki, OVERALL)
    assert alone is not None and wiki is not None
    met = prototype_met(files_alone, files_plus_wiki)
    lines += [
        "",
        "## The prototype",
        "",
        f"{'**Met.**' if met else '**Not met.**'} Files plus wiki scores {wiki.mean:.2f} overall "
        f"against files alone's {alone.mean:.2f}, over {wiki.runs} and {alone.runs} runs. "
        "The phone demo, the criterion's other part, is the owner's to confirm.",
    ]
    return "\n".join(lines) + "\n"


def render_private(files_alone: Sequence[RunResults], files_plus_wiki: Sequence[RunResults]) -> str:
    """The private verdict: every question's outcome in every run, and each failed answer."""
    configurations = {FILES_ALONE: files_alone, FILES_PLUS_WIKI: files_plus_wiki}
    lines = [
        "# v0.13 verdict, private: the failure analysis",
        "",
        "Real data: keep this under the data root.",
        "",
        "## Each question, run by run (correct?)",
        "",
        "| Question | Category | Files alone | Files plus wiki | Asked |",
        "|---|---|---|---|---|",
    ]
    for q in files_alone[0].questions:
        outcomes = [
            ", ".join(_outcome(run, q.id) for run in runs) for runs in configurations.values()
        ]
        lines.append(f"| {q.id} | {q.category} | {' | '.join(outcomes)} | {_cell(q.question)} |")
    for name, runs in configurations.items():
        for number, run in enumerate(runs, start=1):
            for q in run.questions:
                if q.correct is True:
                    continue
                lines += ["", f"## {name}, run {number}: {q.id} ({q.category})", ""]
                lines += _failure(q)
    return "\n".join(lines) + "\n"


def _failure(q: QuestionResult) -> list[str]:
    lines = [
        f"- **Question:** {q.question}",
        f"- **Expected:** {q.expected_answer}",
        f"- **Answer:** {q.answer or '(none)'}",
    ]
    if q.error:
        lines.append(f"- **Error:** {q.error}")
    if q.judge_reasoning:
        lines.append(f"- **Judge:** {q.judge_reasoning}")
    lines.append(f"- **Evidence:** {', '.join(_evidence(q, e) for e in q.evidence) or 'none'}")
    if q.unknown_citations:
        lines.append(f"- **Unknown citations:** {', '.join(q.unknown_citations)}")
    if q.irrelevant_citations:
        lines.append(f"- **Irrelevant citations:** {', '.join(q.irrelevant_citations)}")
    return lines


def _evidence(q: QuestionResult, episode_id: str) -> str:
    if episode_id in q.cited:
        state = "cited"
    elif q.evidence_opened is None:
        state = "not cited; tool calls not recorded"
    elif episode_id in q.evidence_opened:
        state = "opened, not cited"
    else:
        state = "never opened"
    return f"`{episode_id}` ({state})"


def _outcome(run: RunResults, question_id: str) -> str:
    found = [q for q in run.questions if q.id == question_id]
    if not found:
        return "-"
    return "yes" if found[0].correct is True else "no"


def _answers(runs: Sequence[RunResults]) -> list[QuestionResult]:
    return [q for run in runs for q in run.questions]


def _cited_opened(answers: Sequence[QuestionResult]) -> float | None:
    recorded = [q for q in answers if q.cited_opened is not None]
    cited = sum(len(q.cited) for q in recorded)
    return sum(len(q.cited_opened or []) for q in recorded) / cited if cited else None


def _total_cost(answers: Sequence[QuestionResult]) -> float | None:
    costs = [q.cost_usd for q in answers if q.cost_usd is not None]
    return sum(costs) if costs else None


def _label(configuration: str) -> str:
    return {FILES_ALONE: "Files alone", FILES_PLUS_WIKI: "Files plus wiki"}[configuration]


def _spread(value: Spread | None) -> str:
    return "n/a" if value is None else f"{value.mean:.2f} ({value.low:.2f} to {value.high:.2f})"


def _difference(reference: Spread | None, other: Spread | None) -> str:
    if reference is None or other is None:
        return "n/a"
    mark = " (counts)" if counts(reference, other) else ""
    return f"{other.mean - reference.mean:+.2f}{mark}"


def _score(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _dollars(value: float | None, places: int = 2) -> str:
    return "n/a" if value is None else f"${value:.{places}f}"


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
