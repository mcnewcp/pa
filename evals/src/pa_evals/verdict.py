"""The real-data verdict (#100): files plus wiki against files alone, over the same cut.

It comes in two parts. The public verdict, committed to the repository, holds numbers only:
correctness by category with its spread, evidence recall, citation validity, cost, the search
decision and the prototype's outcome. Nothing from the real-data eval set reaches it: no
question, expected answer, answer, reasoning, or question or episode id, and categories are the
eval set's fixed names. Nor does the live wiki's history: the cut names the live wiki, never its
commit. The private verdict, kept under the data root, holds the failure analysis: each failed
answer, with what its session never opened.

Each configuration is asked over at least 3 runs, so a difference can be told from noise.

**Search** (a search engine for v0.13.1) is needed only when, in some configuration, both
hold: evidence recall on the real-data eval set is lower than on the scale corpus by more than
the spread of either, and most of the evidence the answers missed never reached their sessions,
so it was never found, rather than found and left uncited. When recall is lower but no answer
recorded which evidence its session opened, it can't be decided.

**The prototype's eval part** is met when files plus wiki scores at least as well overall as
files alone, as the mean over their runs. The phone demo, its other part, is the owner's to
confirm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pa_core.errors import PaError
from pa_evals.comparison import (
    OVERALL,
    Spread,
    answers,
    correctness,
    counts,
    format_difference,
    format_spread,
    table_cell,
)
from pa_evals.report import format_cost, format_score, format_turns
from pa_evals.results import QuestionResult, RunResults, cited_opened, median

FILES_ALONE = "files-alone"
FILES_PLUS_WIKI = "files-plus-wiki"
MIN_RUNS = 3
"""Each configuration's runs, at the least (#100)."""
NEVER_FOUND_SHARE = 0.5
"""More than this share of the evidence missed never reached the sessions: it was never found."""
SETUP_SHOWN = ("agent", "agent model", "judge", "judge model", "commit", "as-of method")
"""The settings of a run's setup the public verdict shows; the rest of it is prose."""


class VerdictError(PaError):
    """Run directories that don't make a verdict."""


@dataclass(frozen=True)
class ConfigurationRuns:
    """The runs of each eval configuration a verdict compares."""

    files_alone: Sequence[RunResults]
    files_plus_wiki: Sequence[RunResults]

    def by_name(self) -> dict[str, Sequence[RunResults]]:
        """Each configuration's name and runs, files alone first."""
        return {FILES_ALONE: self.files_alone, FILES_PLUS_WIKI: self.files_plus_wiki}


@dataclass(frozen=True)
class SearchDecision:
    """One configuration's evidence recall on the real-data eval set against the scale
    corpus's, and what became of the evidence its answers missed."""

    real: Spread | None
    scale: Spread | None
    recorded: int
    """Answers whose tool calls were recorded, so what their sessions opened is known."""
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

    @property
    def undecided(self) -> bool:
        """Whether recall is lower, but nothing says whether the missed evidence was found."""
        return self.lower and self.never_found_share is None


@dataclass(frozen=True)
class PrototypeOutcome:
    """Overall correctness in each configuration, which decides the prototype's eval part."""

    files_alone: Spread
    files_plus_wiki: Spread

    @property
    def met(self) -> bool:
        return self.files_plus_wiki.mean >= self.files_alone.mean - 1e-9


def check_runs(name: str, configuration: str, runs: Sequence[RunResults]) -> None:
    """Refuse runs that aren't all of `configuration` (given by the option `name`), that
    weren't asked over a cut, or that are fewer than MIN_RUNS."""
    others = sorted({run.configuration or "unnamed" for run in runs} - {configuration})
    if others:
        raise VerdictError(f"{name} holds runs of {', '.join(others)}, not {configuration}")
    if any(run.cut is None for run in runs):
        raise VerdictError(f"{name} holds a run that wasn't asked over a cut")
    if len(runs) < MIN_RUNS:
        raise VerdictError(
            f"{name} holds {len(runs)} run{'s' if len(runs) != 1 else ''}; a verdict needs "
            f"{MIN_RUNS} runs of each configuration"
        )


def check_same_cut(runs: Sequence[RunResults]) -> None:
    names = {run.cut.name for run in runs if run.cut is not None}
    if len(names) != 1:
        raise VerdictError(
            f"the runs weren't all asked over the same cut ({', '.join(sorted(names))})"
        )


def overall_spread(runs: Sequence[RunResults], score: str) -> Spread | None:
    """An overall score's mean and spread over the runs that have it."""
    values = [getattr(run.overall, score) for run in runs]
    present = [value for value in values if value is not None]
    return Spread.over(present) if present else None


def search_decision(real: Sequence[RunResults], scale: Sequence[RunResults]) -> SearchDecision:
    recorded = missed = never_found = 0
    for q in answers(real):
        if q.evidence_opened is None:
            continue
        recorded += 1
        uncited = [e for e in q.evidence if e not in q.cited]
        missed += len(uncited)
        never_found += sum(e not in q.evidence_opened for e in uncited)
    return SearchDecision(
        overall_spread(real, "evidence_recall"),
        overall_spread(scale, "evidence_recall"),
        recorded,
        missed,
        never_found,
    )


def prototype_outcome(real: ConfigurationRuns) -> PrototypeOutcome:
    files_alone = correctness(real.files_alone, OVERALL)
    files_plus_wiki = correctness(real.files_plus_wiki, OVERALL)
    assert files_alone is not None and files_plus_wiki is not None
    return PrototypeOutcome(files_alone, files_plus_wiki)


def render_public(real: ConfigurationRuns, scale: ConfigurationRuns) -> str:
    """The public verdict: numbers only."""
    first = real.files_alone[0]
    assert first.cut is not None
    cut = first.cut
    asked = {c: sum(q.category == c for q in first.questions) for c in first.categories}
    versions = sorted({run.wiki.librarian_version for run in real.files_plus_wiki if run.wiki})
    settings = [
        f"  - {key.capitalize()}: {first.setup[key]}" for key in SETUP_SHOWN if key in first.setup
    ]
    lines = [
        "# v0.13 verdict: the wiki on real data",
        "",
        "Numbers only. The questions, answers and failure analysis are in the private verdict "
        "under the data root.",
        "",
        f"- **The cut:** taken {cut.as_of.strftime('%Y-%m-%d %H:%M UTC')}, {cut.episodes} "
        f"episodes{' and the live wiki' if cut.wiki_commit else ', no wiki'}.",
        f"- **The set:** {len(first.questions)} questions, asked at the cut's time.",
        f"- **The runs:** files alone {len(real.files_alone)}, files plus wiki "
        f"{len(real.files_plus_wiki)}"
        + (f" (librarian version {', '.join(versions)})" if versions else "")
        + ".",
        *(["- **The setup:**", *settings] if settings else []),
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
        files_alone = correctness(real.files_alone, category)
        files_plus_wiki = correctness(real.files_plus_wiki, category)
        count = len(first.questions) if category == OVERALL else asked[category]
        lines.append(
            f"| {category} ({count}) | {format_spread(files_alone)} | "
            f"{format_spread(files_plus_wiki)} | "
            f"{format_difference(files_alone, files_plus_wiki)} |"
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
        cells = [format_spread(overall_spread(runs, score)) for runs in real.by_name().values()]
        lines.append(f"| {label} | {' | '.join(cells)} |")
    rows = {
        "Cited episodes opened": lambda runs: format_score(cited_opened(answers(runs))),
        "Total cost": lambda runs: format_dollars(total_cost(answers(runs))),
        "Per answer, median cost": lambda runs: format_cost(
            median([q.cost_usd for q in answers(runs)])
        ),
        "Per answer, median turns": lambda runs: format_turns(
            median([q.turns for q in answers(runs)])
        ),
        "Failed answers (agent or judge)": lambda runs: str(
            sum(q.status == "failed" for q in answers(runs))
        ),
    }
    for label, cell in rows.items():
        lines.append(f"| {label} | {' | '.join(cell(runs) for runs in real.by_name().values())} |")

    scale_runs = scale.by_name()
    decisions = {
        name: search_decision(runs, scale_runs[name]) for name, runs in real.by_name().items()
    }
    lines += [
        "",
        "## Search",
        "",
        "Evidence recall on the real-data eval set against the scale corpus's (v0.12.1), in the "
        "same configuration, and of the evidence the answers left uncited, how much never "
        "reached their sessions.",
        "",
        "| | Real-data eval set | Scale corpus | Lower by more than the spread | Missed evidence "
        "never opened |",
        "|---|---|---|---|---|",
    ]
    for name, decision in decisions.items():
        lines.append(
            f"| {_label(name)} | {format_spread(decision.real)} | "
            f"{format_spread(decision.scale)} | {'yes' if decision.lower else 'no'} | "
            f"{_never_opened(decision)} |"
        )
    lines += ["", _search_conclusion(decisions)]

    prototype = prototype_outcome(real)
    lines += [
        "",
        "## The prototype",
        "",
        f"{'**Met.**' if prototype.met else '**Not met.**'} Files plus wiki scores "
        f"{prototype.files_plus_wiki.mean:.2f} overall against files alone's "
        f"{prototype.files_alone.mean:.2f}, over {prototype.files_plus_wiki.runs} and "
        f"{prototype.files_alone.runs} runs. The phone demo, the criterion's other part, is the "
        "owner's to confirm.",
    ]
    return "\n".join(lines) + "\n"


def render_private(real: ConfigurationRuns) -> str:
    """The private verdict: every question's outcome in every run, and each failed answer."""
    return failure_analysis(
        "# v0.13 verdict, private: the failure analysis",
        real.by_name(),
        columns=[_label(name) for name in real.by_name()],
    )


def failure_analysis(
    title: str,
    compared: Mapping[str, Sequence[RunResults]],
    *,
    columns: Sequence[str] | None = None,
    before: Sequence[str] = (),
) -> str:
    """A private failure analysis under `title`, of the sets of runs `compared`, each by its
    name: every question's outcome in every run of each set, in a column per set (headed by
    `columns`, else by the names), then each answer that wasn't correct, with what its
    session never opened. The lines `before` are a section of the caller's, put first. It
    quotes questions and answers: it is kept under the data root."""
    headed = columns or [name.capitalize() for name in compared]
    lines = [
        title,
        "",
        "Real data: keep this under the data root.",
        "",
        *before,
        *([""] if before else []),
        "## Each question, run by run (correct?)",
        "",
        f"| Question | Category | {' | '.join(headed)} | Asked |",
        f"|---|---|{'---|' * len(headed)}---|",
    ]
    for q in next(iter(compared.values()))[0].questions:
        outcomes = [", ".join(_outcome(run, q.id) for run in runs) for runs in compared.values()]
        lines.append(
            f"| {q.id} | {q.category} | {' | '.join(outcomes)} | {table_cell(q.question)} |"
        )
    for name, runs in compared.items():
        for number, run in enumerate(runs, start=1):
            for q in run.questions:
                if q.correct is True:
                    continue
                lines += ["", f"## {name}, run {number}: {q.id} ({q.category})", ""]
                lines += _failure(q)
    return "\n".join(lines) + "\n"


def _never_opened(decision: SearchDecision) -> str:
    share = decision.never_found_share
    if share is not None:
        return f"{decision.never_found} of {decision.missed} ({share:.2f})"
    return "n/a (not recorded)" if decision.recorded == 0 else "n/a (none missed)"


def _search_conclusion(decisions: dict[str, SearchDecision]) -> str:
    def named(names: list[str]) -> str:
        return ", ".join(_label(name).lower() for name in names)

    needed = [name for name, decision in decisions.items() if decision.needed]
    undecided = [name for name, decision in decisions.items() if decision.undecided]
    if needed:
        return (
            f"**Needed:** in {named(needed)}, recall is lower than the scale corpus's by more "
            "than the spread, and most of the missed evidence was never opened. Plan v0.13.1's "
            "search engine."
        )
    if undecided:
        return (
            f"**Can't be decided:** in {named(undecided)}, recall is lower than the scale "
            "corpus's by more than the spread, but no answer recorded which evidence its session "
            "opened, so evidence never found can't be told from evidence found and left uncited."
        )
    return (
        "**Not needed:** in neither configuration is recall lower than the scale corpus's by "
        "more than the spread while most of the missed evidence was never opened."
    )


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
    asked = [q for q in run.questions if q.id == question_id]
    if not asked:
        return "-"
    return "yes" if asked[0].correct is True else "no"


def total_cost(results: Sequence[QuestionResult]) -> float | None:
    costs = [q.cost_usd for q in results if q.cost_usd is not None]
    return sum(costs) if costs else None


def _label(configuration: str) -> str:
    return {FILES_ALONE: "Files alone", FILES_PLUS_WIKI: "Files plus wiki"}[configuration]


def format_dollars(value: float | None) -> str:
    """A total in dollars to the cent (an answer's cost, format_cost, is to the tenth of one)."""
    return "n/a" if value is None else f"${value:.2f}"
