"""The proposal-answer fixture (spec #140, stories 40 to 43, 49 to 52, and 90): the owner asks
what the PA needs, answers a proposal in chat, and the next librarian run applies the answer.

It is a data root with a starting wiki and one stage of payloads, in
`fixtures/proposal-answer/`:

- `seed/`: mail and daily notes, already observed and folded into the starting wiki (`wiki/`,
  whose `{ep:<payload>}` citations name the seed's payloads by file stem). That wiki has JR
  and Jordan Reyes on two pages, each with an open loop, and a remodeler that owes a quote.
  Its `proposals.md` holds two open proposals, as a dreaming run would have left them: P1, a
  change ("Are JR and Jordan Reyes the same person?"), and P2, a question about the quote.

The owner's two exchanges are not files: the fictional owner's turns are in this module
(`ASKING`, `ANSWERING`), and the assistant's replies are whatever an assistant gives. The
hand-run test asks the real assistant, over the seeded wiki; the fast tests use the replies
written here. Either way each exchange is captured as the capture hooks would write it, so the
librarian's run takes them from L0 like any chat.

The run should leave P1 under Answered, accepted, citing the owner's answer last; the two
pages merged into one that goes by both names and keeps both loops; and P2 open, as it was.

The fast tests check the fixture itself and its checks. The real assistant's turns and the
real librarian's run are hand-run, since they use the real model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import inbox_normalizer, ingest
from pa_core.librarian.batch import production_batch
from pa_core.librarian.lint import Thresholds, lint
from pa_core.librarian.metadata import L0Metadata
from pa_core.librarian.run import RunOutcome, last_cursor
from pa_core.librarian.wiki import (
    PROPOSALS,
    Page,
    Proposal,
    citations,
    links,
    pages,
    read_proposals,
    slugify,
)
from pa_core.normalizers.assistant_chat import encode_exchange, exchange_episode_id
from pa_core.owner import Owner
from pa_home import librarian
from pa_home.agent import ClaudeAgent
from pa_home.config import DataRoot, load_librarian_config
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

DEPLOY = Path(__file__).resolve().parents[1]
FIXTURE = DEPLOY / "tests" / "fixtures" / "proposal-answer"
REPLAYS = Path.home() / ".local" / "share" / "pa-home" / "replays"
"""PA_REPLAYS in lib.sh."""

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
TIME_ZONE = ZoneInfo("America/Chicago")

SEED_CAPTURED_AT = datetime(2026, 11, 12, 12, 0, tzinfo=TIME_ZONE)
"""When the seed's mail is captured (a daily note's capture time is its own)."""
CHAT_CLOCK = datetime(2026, 11, 14, 19, 30, tzinfo=TIME_ZONE)
""""Now" for the assistant, and when the owner's first exchange starts."""
CHATS_CAPTURED_AT = datetime(2026, 11, 14, 20, 0, tzinfo=TIME_ZONE)
RUN_CLOCK = datetime(2026, 11, 14, 21, 0, tzinfo=TIME_ZONE)
""""Now" for the run after the owner's answer."""

ASKING = "What do you need from me?"
ANSWERING = "On P1: yes, JR is Jordan Reyes. Same person, JR is just what everyone calls him."
"""The fictional owner's two turns, each opening an exchange of its own."""
CHAT_SESSION = "2d6f0b9c-7a41-4c3e-9f58-b1e07a3c64d2"
EXCHANGES = {
    "asked": ("9a3e5c71-0b2d-4f86-a7c9-3d1e8f402b65", ASKING),
    "answered": ("c58b1f04-6e9a-4d27-8b3c-f07a2e915d48", ANSWERING),
}
"""The owner's exchanges, by name: the id of the owner's message, and what the owner said."""

JORDAN = "jordan.reyes@example.net"
MERGED_SLUGS = ("jr", "jordan-reyes")
"""The two pages P1 asks about; an accepted P1 leaves one of them, going by both names."""
P1_CITES = ("2026-11-09_daily-note", "trailer-offer")
P2_TEXT = "Did [[birchwood-remodeling]] send the revised mudroom quote?"

_PLACEHOLDER = re.compile(r"\{ep:([A-Za-z0-9_.-]+)\}")


def episode_ids() -> dict[str, str]:
    """Every seed payload's episode id, by its file stem, and each of the owner's exchanges',
    by its name."""
    ids: dict[str, str] = {}
    for payload, inbox_name in _payloads():
        normalize = inbox_normalizer(inbox_name, OWNER)
        envelope = normalize(payload.read_bytes(), captured_at=SEED_CAPTURED_AT)
        ids[payload.stem] = envelope.episode_id
    for name, (message, _) in EXCHANGES.items():
        ids[name] = exchange_episode_id(CHAT_SESSION, message)
    return ids


def seed_proposal_answer_fixture(root: Path) -> dict[str, str]:
    """Makes `root` a data root holding the seed's episodes and the starting wiki, committed
    with the seed's cursor, so the next run takes only what's captured after it. Returns every
    episode id `episode_ids` gives."""
    ids = episode_ids()
    data_root = DataRoot(root)
    for payload, inbox_name in _payloads():
        target = data_root.inbox / inbox_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payload, target)
    _ingest(root, SEED_CAPTURED_AT)
    with tempfile.TemporaryDirectory(prefix="pa-proposal-answer-") as directory:
        # Laid out as an eval wiki is, with the citations filled in, then seeded as one.
        seed = Path(directory)
        for source in sorted((FIXTURE / "wiki").rglob("*.md")):
            target = seed / "wiki" / source.relative_to(FIXTURE / "wiki")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_cited(source.read_text(encoding="utf-8"), ids), encoding="utf-8")
        librarian.seed(data_root, seed, owner=OWNER, now=lambda: RUN_CLOCK)
    return ids


def owner_chats(ask: Callable[[str], str]) -> dict[str, str]:
    """The assistant's reply to each of the owner's turns, by the exchange's name: `ask` gives
    the assistant's reply to one turn. Each turn opens a session of its own, as a question from
    the phone does, so the assistant has only the wiki and L0 to go by."""
    return {name: ask(turn) for name, (_, turn) in EXCHANGES.items()}


def capture_exchanges(root: Path, replies: Mapping[str, str]) -> None:
    """Captures the owner's exchanges into the data root at `root`, each with the assistant's
    reply from `replies`, as the capture hooks write them, and ingests them."""
    inbox = DataRoot(root).inbox / "assistant_chat"
    inbox.mkdir(parents=True, exist_ok=True)
    for number, (name, (message, turn)) in enumerate(EXCHANGES.items()):
        started = CHAT_CLOCK + timedelta(minutes=2 * number)
        payload = encode_exchange(
            session_id=CHAT_SESSION,
            message_id=message,
            started_at=started.isoformat(),
            ended_at=(started + timedelta(seconds=40)).isoformat(),
            turns=[("owner", turn), ("assistant", replies[name])],
        )
        (inbox / f"{name}.json").write_bytes(payload)
    _ingest(root, CHATS_CAPTURED_AT)


def _ingest(root: Path, captured_at: datetime) -> None:
    data_root = DataRoot(root)
    store = FilesystemL0(data_root.l0)
    data_root.catalog.parent.mkdir(parents=True, exist_ok=True)
    with open_catalog(data_root.catalog, store) as catalog:
        summary = ingest(data_root.inbox, store, catalog, OWNER, now=lambda: captured_at)
    assert summary.quarantined == 0, "the fixture has a malformed payload"


def _payloads() -> list[tuple[Path, PurePosixPath]]:
    """The seed's payloads, each with its name in the inbox (its source's folder, then it)."""
    directory = FIXTURE / "seed"
    return [
        (payload, PurePosixPath(payload.relative_to(directory).as_posix()))
        for payload in sorted(directory.rglob("*"))
        if payload.is_file()
    ]


def _cited(text: str, ids: Mapping[str, str]) -> str:
    """`text` with each `{ep:<name>}` filled in as that episode's citation."""
    return _PLACEHOLDER.sub(lambda m: f"[ep:{ids[m[1]]}]", text)


def _l0(root: Path) -> L0Metadata:
    return L0Metadata.of(FilesystemL0(DataRoot(root).l0).envelopes())


# What a good assistant says, for the fast tests: the hand-run test asks the real one.
LISTING = (
    "Two things are waiting on you. P1: are JR and Jordan Reyes the same person? P2: did "
    "Birchwood Remodeling send the revised mudroom quote? They promised it by Nov 10 "
    "{ep:quote-promised}."
)
RESTATEMENT = (
    "Noted on P1: JR and Jordan Reyes are the same person. The wiki will show it after the "
    "next librarian run."
)


def good_replies(ids: Mapping[str, str]) -> dict[str, str]:
    return {"asked": _cited(LISTING, ids), "answered": RESTATEMENT}


def test_the_seeded_wiki_lints_clean_and_holds_two_open_proposals(tmp_path: Path):
    ids = seed_proposal_answer_fixture(tmp_path)
    wiki = DataRoot(tmp_path).wiki

    report = lint(
        wiki,
        l0=_l0(tmp_path),
        owner=OWNER,
        thresholds=Thresholds(today=RUN_CLOCK.date(), page_threshold=3, loop_age_days=30),
    )

    assert report.errors == ()
    # The quote was due on Nov 10, which is why the librarian asks about it.
    assert [str(warning) for warning in report.warnings] == [
        "orgs/birchwood-remodeling.md:15: a loop past due: due 2026-11-10, 4 days ago"
    ]
    assert [(p.id, p.kind, p.is_open) for p in read_proposals(wiki)] == [
        ("P1", "change", True),
        ("P2", "question", True),
    ]
    assert read_proposals(wiki)[0].citations == tuple(ids[name] for name in P1_CITES)
    assert (wiki / "index.md").read_text().splitlines()[2] == "Open proposals: 2, in [[proposals]]"


def test_the_run_after_the_owners_exchanges_takes_exactly_them(tmp_path: Path):
    ids = seed_proposal_answer_fixture(tmp_path)
    data_root = DataRoot(tmp_path)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    capture_exchanges(tmp_path, good_replies(ids))

    batch = production_batch(
        FilesystemL0(data_root.l0), after=last_cursor(repository), time_zone=TIME_ZONE
    )

    assert batch is not None
    assert [e.episode_id for e in batch.episodes] == [ids["asked"], ids["answered"]]
    assert [e.owner_turns for e in batch.episodes] == [(ASKING,), (ANSWERING,)]
    assert {e.day for e in batch.episodes} == {date(2026, 11, 14)}
    # Both are the owner's chat exchanges, which is what lint lets answer a proposal.
    assert _l0(tmp_path).owner_exchanges(OWNER) == {ids["asked"], ids["answered"]}


def test_the_checks_find_everything_the_answer_must_do_missing_from_the_starting_wiki(
    tmp_path: Path,
):
    ids = seed_proposal_answer_fixture(tmp_path)

    assert answer_problems(DataRoot(tmp_path).wiki, ids) == [
        "P1 is still open",
        "people/jordan-reyes.md doesn't go by both JR and Jordan Reyes: ['jordan', 'jordan-reyes']",
        "people/jordan-reyes.md doesn't cite the owner's answer",
        "people/jordan-reyes.md doesn't carry JR's loop for the tile saw",
        "people/jr.md is still a page of its own",
        "days/2026-11-05.md still links [[jr]]",
        "days/2026-11-09.md still links [[jr]]",
        "index.md still links [[jr]]",
        "people/jr.md still links [[jr]]",
        "proposals.md still links [[jr]]",
        "index.md doesn't open with one open proposal: 'Open proposals: 2, in [[proposals]]'",
    ]


TRAILER_LOOP = (
    "- [ ] me → [[jordan-reyes]]: say which weekend I want the trailer · asked 2026-11-03 "
    "{ep:trailer-offer}\n"
)
"""Jordan Reyes's own loop, as his page has it from the start."""
SAW_LOOP = (
    "- [ ] [[jordan-reyes]] → me: drop off the tile saw · promised 2026-11-05 · due 2026-11-20 "
    "{ep:2026-11-05_daily-note}\n"
)
"""JR's loop, as the merged page carries it."""
SAME_PERSON = "JR and Jordan Reyes are the same person, by the owner's word. {ep:answered}\n"
MERGED_PAGE = (
    """\
---
type: person
aliases: [Jordan, JR]
emails: [jordan.reyes@example.net]
status: active
summary: Lending me his utility trailer for the mulch run, and dropping off the tile saw.
updated: 2026-11-14
---
# Jordan Reyes

## Current state
Offered his utility trailer, which is free on Saturday, Nov 21. {ep:2026-11-09_daily-note}
Will drop off the tile saw by Nov 20. {ep:2026-11-05_daily-note}

## Open loops
"""
    + TRAILER_LOOP
    + SAW_LOOP
    + "\n## Notes\n"
    + SAME_PERSON
)
"""Jordan Reyes's page as an accepted P1 leaves it, citing payloads and exchanges by name."""


def resolved_proposals(how: str = "accepted", last: str = "answered") -> str:
    """The starting `proposals.md` with P1 resolved: answered `how` on the day of the owner's
    answer, citing `last` last."""
    starting = (FIXTURE / "wiki" / PROPOSALS).read_text(encoding="utf-8")
    [opened] = [line for line in starting.splitlines() if " P1 · " in line]
    resolved = opened.replace("- [ ]", "- [x]").replace(
        "· proposed 2026-11-12 ", f"· proposed 2026-11-12 · {how} 2026-11-14 "
    )
    return starting.replace(f"{opened}\n", "") + f"{resolved} {{ep:{last}}}\n"


def test_the_checks_pass_the_wiki_a_run_leaves_when_it_applies_the_answer(
    tmp_path: Path, stand_in_claude: Path
):
    """The librarian's half, dry: a real run through the stand-in `claude`, scripted to do
    what the instructions say. It shows the host accepts that work and the checks pass it."""
    root = tmp_path / "root"
    ids = seed_proposal_answer_fixture(root)
    capture_exchanges(root, good_replies(ids))
    written = {
        "people/jordan-reyes.md": MERGED_PAGE,
        "people/jr.md": "",
        PROPOSALS: resolved_proposals(),
    }
    actions = [{"write": path, "text": _cited(text, ids)} for path, text in written.items()]
    (tmp_path / "script.json").write_text(json.dumps({"consolidation": actions}))
    config = load_librarian_config({"PA_LIBRARIAN_COMMAND": str(stand_in_claude)})

    outcome = librarian.run(
        DataRoot(root), owner=OWNER, time_zone=TIME_ZONE, config=config, now=lambda: RUN_CLOCK
    )

    assert outcome.commit is not None
    wiki = DataRoot(root).wiki
    assert answer_problems(wiki, ids) == []
    # The host finished the merge: the emptied page is gone, and its links go to the other.
    assert not (wiki / "people" / "jr.md").exists()
    assert "[[jordan-reyes|jr]]" in (wiki / "days" / "2026-11-05.md").read_text()


# What a run could leave instead: a change to the wiki the dry run leaves (a file's new text,
# citing by name), and the problems the checks must find in it.
DEFECTS: dict[str, tuple[dict[str, str], list[str]]] = {
    "the proposal declined": (
        {PROPOSALS: resolved_proposals("declined")},
        ["P1 is declined, not accepted"],
    ),
    "the proposal resolved citing the owner's question": (
        {PROPOSALS: resolved_proposals(last="asked")},
        ["P1 doesn't cite the owner's answer last"],
    ),
    "the proposal resolved on the wrong day": (
        {PROPOSALS: resolved_proposals().replace("accepted 2026-11-14", "accepted 2026-11-12")},
        ["P1 is answered 2026-11-12, not on 2026-11-14, the day of the owner's answer"],
    ),
    "the answer applied to no page": (
        {"people/jordan-reyes.md": MERGED_PAGE.replace(SAME_PERSON, "")},
        ["people/jordan-reyes.md doesn't cite the owner's answer"],
    ),
    "the merged page under one name": (
        {"people/jordan-reyes.md": MERGED_PAGE.replace("aliases: [Jordan, JR]", "aliases: []")},
        ["people/jordan-reyes.md doesn't go by both JR and Jordan Reyes: ['jordan-reyes']"],
    ),
    "JR's loop dropped in the merge": (
        {"people/jordan-reyes.md": MERGED_PAGE.replace(SAW_LOOP, "")},
        ["people/jordan-reyes.md doesn't carry JR's loop for the tile saw"],
    ),
    "Jordan Reyes's own loop dropped in the merge": (
        {"people/jordan-reyes.md": MERGED_PAGE.replace(TRAILER_LOOP, "")},
        ["people/jordan-reyes.md lost Jordan Reyes's own loop about the trailer"],
    ),
    "the other proposal resolved too": (
        {
            PROPOSALS: resolved_proposals()
            .replace("- [ ] P2", "- [x] P2")
            .replace(
                "· proposed 2026-11-12 {ep:quote-promised}\n",
                "· proposed 2026-11-12 · answered 2026-11-14 {ep:quote-promised} {ep:answered}\n",
            )
        },
        [
            "P2 is answered, though the owner said nothing about it",
            "index.md doesn't open with one open proposal: 'Open proposals: 0'",
        ],
    ),
}


@pytest.mark.parametrize("defect", DEFECTS, ids=list(DEFECTS))
def test_the_checks_find_each_way_the_run_could_fall_short(tmp_path: Path, defect: str):
    ids = seed_proposal_answer_fixture(tmp_path)
    capture_exchanges(tmp_path, good_replies(ids))
    wiki = DataRoot(tmp_path).wiki
    changes, expected = DEFECTS[defect]
    # The wiki the dry run leaves, written by hand as the host would finish it.
    (wiki / "people" / "jr.md").unlink()
    for path in sorted(wiki.rglob("*.md")):
        path.write_text(path.read_text().replace("[[jr]]", "[[jordan-reyes|jr]]"))
    left = {"people/jordan-reyes.md": MERGED_PAGE, PROPOSALS: resolved_proposals(), **changes}
    for path, text in left.items():
        (wiki / path).write_text(_cited(text, ids).replace("[[jr]]", "[[jordan-reyes|jr]]"))
    index = wiki / "index.md"
    still_open = sum(proposal.is_open for proposal in read_proposals(wiki))
    index.write_text(
        index.read_text().replace(
            "Open proposals: 2, in [[proposals]]",
            f"Open proposals: {still_open}" + (", in [[proposals]]" if still_open else ""),
        )
    )

    assert answer_problems(wiki, ids) == expected


def test_the_checks_pass_a_good_assistants_replies(tmp_path: Path):
    ids = seed_proposal_answer_fixture(tmp_path)

    assert assistant_problems(good_replies(ids), _l0(tmp_path)) == []


# Replies an assistant could give instead, and the problems the checks must find in them.
POOR_REPLIES: dict[str, tuple[dict[str, str], list[str]]] = {
    "a listing that leaves a proposal out": (
        {"asked": "One thing: P1, are JR and Jordan Reyes the same person?"},
        [
            "asked what it needs, the assistant doesn't give P2",
            "asked what it needs, the assistant doesn't name Birchwood",
        ],
    ),
    "a listing that cites a proposal": (
        {"asked": LISTING + " [ep:P1]"},
        ["asked what it needs, the assistant cites P1, which isn't an episode in L0"],
    ),
    "nothing said to be waiting": (
        {"asked": "Nothing is waiting on you right now."},
        [
            "asked what it needs, the assistant doesn't give P1",
            "asked what it needs, the assistant doesn't give P2",
            "asked what it needs, the assistant doesn't name JR",
            "asked what it needs, the assistant doesn't name Jordan Reyes",
            "asked what it needs, the assistant doesn't name Birchwood",
        ],
    ),
    "a restatement that names no proposal": (
        {"answered": "Got it, they're the same person. That will show after the next run."},
        ["no sentence of the assistant's restatement names P1, JR, and Jordan Reyes"],
    ),
    "a restatement spread over two sentences": (
        {
            "answered": "Noted on P1. JR and Jordan Reyes are the same person; the wiki will "
            "show it after the next librarian run."
        },
        ["no sentence of the assistant's restatement names P1, JR, and Jordan Reyes"],
    ),
    "a restatement that doesn't say when the wiki will show it": (
        {"answered": "Noted on P1: JR and Jordan Reyes are the same person."},
        ["the assistant's restatement doesn't say the wiki will show it after the next run"],
    ),
}


@pytest.mark.parametrize("poor", POOR_REPLIES, ids=list(POOR_REPLIES))
def test_the_checks_find_each_way_the_assistant_could_fall_short(tmp_path: Path, poor: str):
    ids = seed_proposal_answer_fixture(tmp_path)
    changed, expected = POOR_REPLIES[poor]
    replies = {**good_replies(ids), **{name: _cited(text, ids) for name, text in changed.items()}}

    assert assistant_problems(replies, _l0(tmp_path)) == expected


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_CLAUDE") != "1",
    reason="calls the real claude CLI; set PA_SMOKE_CLAUDE=1 to run",
)
def test_smoke_the_real_assistant_lists_the_open_proposals_and_restates_the_owners_answer(
    tmp_path: Path,
):
    """The assistant's half alone: two real sessions on the host over the seeded wiki, one for
    each of the owner's turns. Prints both replies and their cost."""
    seed_proposal_answer_fixture(tmp_path)

    replies = owner_chats(_real_assistant(tmp_path))

    problems = assistant_problems(replies, _l0(tmp_path))
    assert not problems, "\n".join(problems)


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_LIBRARIAN") != "1",
    reason="asks the real assistant on the host, then runs the real librarian in its VM; set "
    "PA_SMOKE_LIBRARIAN=1 to run (deploy/home/README.md)",
)
def test_smoke_the_owner_answers_a_proposal_and_the_next_real_run_applies_it():
    """The whole loop with the real model. The real assistant, on the host, is asked what it
    needs and then given the owner's answer, over a copy of the seeded wiki; both exchanges are
    captured as it replied; and one real run in `pa-librarian`, as `deploy/home/librarian.sh
    run` makes it, takes them. The wiki is left in the target directory (under the replays'
    filesystem, which the VM may write) for reading; the test prints the replies and the cost."""
    target = _smoke_target()
    ids = seed_proposal_answer_fixture(target)
    with tempfile.TemporaryDirectory(prefix="pa-proposal-answer-") as directory:
        # The assistant reads a copy in the system's temporary directory, as an eval's does.
        seed_proposal_answer_fixture(Path(directory))
        replies = owner_chats(_real_assistant(Path(directory)))
    problems = assistant_problems(replies, _l0(target))
    capture_exchanges(target, replies)
    config = load_librarian_config({"PA_LIBRARIAN_SANDBOX": str(DEPLOY / "librarian-sandbox.sh")})
    data_root = DataRoot(target)

    outcome = librarian.run(
        data_root, owner=OWNER, time_zone=TIME_ZONE, config=config, now=lambda: RUN_CLOCK
    )

    _report(outcome)
    problems += answer_problems(data_root.wiki, ids)
    assert not problems, f"in {data_root.wiki}:\n" + "\n".join(problems)


_ANY_CITATION = re.compile(r"\[ep:([^\]]*)\]")


def assistant_problems(replies: Mapping[str, str], l0: L0Metadata) -> list[str]:
    """How the assistant's `replies` to the owner's two turns fall short: of giving the open
    proposals when asked what it needs, of restating the owner's answer in one sentence that
    names the proposal, of saying when the wiki will show it, and of citing only episodes."""
    problems: list[str] = []
    listing, restatement = replies["asked"], replies["answered"]
    asked = "asked what it needs, the assistant"
    problems += [f"{asked} doesn't give {name}" for name in ("P1", "P2") if name not in listing]
    problems += [
        f"{asked} doesn't name {name}"
        for name in ("JR", "Jordan Reyes", "Birchwood")
        if not re.search(rf"\b{name}\b", listing)
    ]
    sentences = re.split(r"(?<=[.!?])\s+", restatement)
    named = (r"\bP1\b", r"\bJR\b", r"\bJordan Reyes\b")
    if not any(all(re.search(name, sentence) for name in named) for sentence in sentences):
        problems.append("no sentence of the assistant's restatement names P1, JR, and Jordan Reyes")
    if not re.search(r"\bnext (librarian )?run\b", restatement):
        problems.append(
            "the assistant's restatement doesn't say the wiki will show it after the next run"
        )
    for when, reply in ((asked, listing), ("answered, the assistant", restatement)):
        problems += [
            f"{when} cites {cited}, which isn't an episode in L0"
            for cited in dict.fromkeys(_ANY_CITATION.findall(reply))
            if cited not in l0.episode_ids
        ]
    return problems


def answer_problems(wiki: Path, ids: Mapping[str, str]) -> list[str]:
    """How the wiki at `wiki` falls short of the owner's answer to P1: the proposal resolved
    as accepted citing that answer, the two pages merged into one, and nothing else asked or
    answered."""
    found = {page.slug: page for page in pages(wiki)}
    text = {path: path.read_text(encoding="utf-8") for path in sorted(wiki.rglob("*.md"))}
    asked = {proposal.id: proposal for proposal in read_proposals(wiki)}
    problems = _resolution_problems(asked, ids)

    # The merge: one page, both names, both loops, the owner's answer cited, links rewritten.
    jordan = _page_with_address(found, JORDAN)
    if jordan is None:
        problems.append("no page lists Jordan Reyes's address")
    else:
        names = {jordan.slug, *(slugify(alias) for alias in jordan.aliases)}
        if not set(MERGED_SLUGS) <= names:
            problems.append(
                f"{jordan.path} doesn't go by both JR and Jordan Reyes: {sorted(names)}"
            )
        cited = {
            c for section in jordan.sections for _, line in section.lines for c in citations(line)
        }
        if ids["answered"] not in cited:
            problems.append(f"{jordan.path} doesn't cite the owner's answer")
        if not any(loop.owed_by == jordan.slug and "saw" in loop.what for loop in jordan.loops):
            problems.append(f"{jordan.path} doesn't carry JR's loop for the tile saw")
        if not any(loop.owed_to == jordan.slug and "trailer" in loop.what for loop in jordan.loops):
            problems.append(f"{jordan.path} lost Jordan Reyes's own loop about the trailer")
    gone = [slug for slug in MERGED_SLUGS if jordan is None or slug != jordan.slug]
    problems += [f"people/{slug}.md is still a page of its own" for slug in gone if slug in found]
    problems += [
        f"{path.relative_to(wiki)} still links [[{slug}]]"
        for slug in gone
        for path, body in text.items()
        if slug in links(body)
    ]

    opening = text[wiki / "index.md"].splitlines()[2]
    if opening != "Open proposals: 1, in [[proposals]]":
        problems.append(f"index.md doesn't open with one open proposal: {opening!r}")
    return problems


def _resolution_problems(asked: Mapping[str, Proposal], ids: Mapping[str, str]) -> list[str]:
    """How `proposals.md` falls short: P1 answered as accepted, on the day of the owner's
    answer, keeping its citations and citing that answer last; P2 open as it was; no other."""
    problems: list[str] = []
    first, second = asked.get("P1"), asked.get("P2")
    answered_on = CHAT_CLOCK.date()
    if first is None:
        problems.append("P1 is gone from proposals.md")
    elif first.answer is None:
        problems.append("P1 is still open")
    else:
        if first.answer.how != "accepted":
            problems.append(f"P1 is {first.answer.how}, not accepted")
        if first.answer.day != answered_on:
            problems.append(
                f"P1 is answered {first.answer.day}, not on {answered_on}, the day of the "
                "owner's answer"
            )
        if first.citations[-1] != ids["answered"]:
            problems.append("P1 doesn't cite the owner's answer last")
        if first.citations[:2] != tuple(ids[name] for name in P1_CITES):
            problems.append("P1 lost the episodes it cited")
    if second is None:
        problems.append("P2 is gone from proposals.md")
    elif not second.is_open:
        problems.append("P2 is answered, though the owner said nothing about it")
    elif (second.text, second.citations) != (P2_TEXT, (ids["quote-promised"],)):
        problems.append(f"P2 was changed: {second.line}")
    problems += [f"{name} was added to proposals.md" for name in asked if name not in ("P1", "P2")]
    return problems


def _page_with_address(found: Mapping[str, Page], address: str) -> Page | None:
    return next((page for page in found.values() if address in page.fields.get("emails", ())), None)


def _real_assistant(root: Path) -> Callable[[str], str]:
    """The real assistant over the data root at `root`, asked at `CHAT_CLOCK`: one `claude -p`
    session on the host for each turn, in the rendered agent project."""
    data_root = DataRoot(root)
    agent = ClaudeAgent(model=os.environ.get("PA_SMOKE_CLAUDE_MODEL", "haiku"))

    def ask(turn: str) -> str:
        answer = agent.answer(
            turn, owner=OWNER, l0=data_root.l0, wiki=data_root.wiki, now=CHAT_CLOCK
        )
        print(f"\nowner: {turn}\nassistant (${answer.cost_usd or 0:.2f}): {answer.text}")
        return answer.text

    return ask


def _smoke_target() -> Path:
    """A new directory for the run's data root, on the replays' filesystem (install-wiki.sh),
    the only place besides the live wiki that the librarian's VM may write a wiki."""
    configured = os.environ.get("PA_SMOKE_PROPOSAL_ANSWER_DIR", "").strip()
    if configured:
        target = Path(configured).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        target = REPLAYS / f"proposal-answer-{stamp}"
    assert not target.exists() or not any(target.iterdir()), f"{target} isn't empty"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _report(outcome: RunOutcome) -> None:
    sessions = ", ".join(
        f"{phase}: ${result.cost_usd or 0:.2f}, {result.turns} turns, {result.resumes} resumes"
        for phase, result in outcome.sessions
    )
    cost = sum(result.cost_usd or 0 for _, result in outcome.sessions)
    print(f"\nthe run: committed {outcome.commit}; ${cost:.2f} ({sessions})")
    for warning in outcome.warnings:
        print(f"  warning: {warning}")
