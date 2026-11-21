"""The page-creation fixture (spec #140, stories 53 to 58): one night that would clutter the wiki,
over a wiki that an earlier rule already cluttered.

It is a data root with a starting wiki and two stages of payloads, in `fixtures/page-creation/`:

- `seed/`: two delivery updates from ParcelPost's no-reply address, already observed and
  folded into the starting wiki (`wiki/`, whose `{ep:<payload>}` citations name the seed's
  payloads by file stem). That wiki was written before the page-creation rules: it gives the
  mailbox a person page, holding an open loop for a replacement ParcelPost promised, beside
  ParcelPost's own organization page. Lint now fails that person page, so the next run can't
  commit until its consolidation merges it away.
- `night/`: three days of mail and a daily note, for one run:
  - **the automated mailbox** again, with a delivery update on each of the three days;
  - **a cold request**: Lyle Brandt of BrightPath Solar, whom the owner has never engaged
    with, asking three days running for a call;
  - **a request about an order the owner placed**: Cedar Pine, which the owner never wrote
    to, asking the owner to choose a replacement or a refund for a tent in order #48213;
  - **the control**: Priya Raman, whom the owner wrote to and noted, asking for a campsite
    number.

The night should leave no person page for the mailbox (the one it had merged into
ParcelPost's page, with its loop, and no link to its slug left without a page), none for Lyle
Brandt and no loop for his request, an open loop for Cedar Pine's, and a page and a loop for
Priya Raman.

The fast tests check the fixture itself and its checks. The real librarian's run over it is
hand-run, since it uses the real model.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import inbox_normalizer, ingest
from pa_core.librarian.batch import cursor_through, production_batch
from pa_core.librarian.identity import identity_table
from pa_core.librarian.index import write_index
from pa_core.librarian.lint import L0Metadata, LintReport, Thresholds, lint
from pa_core.librarian.run import CURSOR_FIELD, RunOutcome, last_cursor
from pa_core.librarian.wiki import PERSON, Page, citations, day_logs, links, pages, slugify
from pa_core.owner import Owner
from pa_home import librarian
from pa_home.config import DataRoot, load_librarian_config
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

DEPLOY = Path(__file__).resolve().parents[1]
FIXTURE = DEPLOY / "tests" / "fixtures" / "page-creation"
REPLAYS = Path.home() / ".local" / "share" / "pa-home" / "replays"
"""PA_REPLAYS in lib.sh."""

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
TIME_ZONE = ZoneInfo("America/Chicago")

STAGES = ("seed", "night")
CAPTURED_AT = {
    "seed": datetime(2026, 10, 7, 12, 0, tzinfo=TIME_ZONE),
    "night": datetime(2026, 10, 14, 20, 0, tzinfo=TIME_ZONE),
}
"""When each stage's mail is captured (the daily note's capture time is its own)."""
RUN_CLOCK = datetime(2026, 10, 14, 21, 0, tzinfo=TIME_ZONE)
""""Now" for the night's run."""

MAILBOX = "no-reply@parcelpost.example"
MAILBOX_SLUG = "parcelpost-tracking"
"""The slug the identity table suggests for the mailbox's display name, and its page's."""
MAILBOX_PAGE = f"people/{MAILBOX_SLUG}.md"
"""The person page the starting wiki has for the mailbox."""
MAILBOX_LOOP = "drill-delivered"
"""The seed mail that opened the loop on the mailbox's page: a replacement by Oct 20."""
PARCELS = ("parcel-shipped", "parcel-out-for-delivery", "parcel-delivered")
COLD_SENDER = "lyle.brandt@brightpathsolar.example"
COLD_REQUESTS = ("solar-pitch", "solar-follow-up", "solar-last-note")
ORDER_REQUEST = "tent-order-problem"
FRIEND = "priya.raman@example.org"
FRIENDS_REQUEST = ("stove-reply", "2026-10-14_daily-note")
"""Her mail asking for the campsite number, and the owner's note of owing it."""
OWNERS_EPISODES = ("stove-ask", "2026-10-14_daily-note")

_PLACEHOLDER = re.compile(r"\{ep:([A-Za-z0-9_.-]+)\}")


def episode_ids() -> dict[str, str]:
    """Every fixture payload's episode id, by its file stem."""
    ids: dict[str, str] = {}
    for stage in STAGES:
        for payload, inbox_name in _payloads(stage):
            normalize = inbox_normalizer(inbox_name, OWNER)
            envelope = normalize(payload.read_bytes(), captured_at=CAPTURED_AT[stage])
            ids[payload.stem] = envelope.episode_id
    return ids


def seed_page_creation_fixture(root: Path) -> dict[str, str]:
    """Makes `root` a data root holding the seed's episodes and the starting wiki, committed
    with the seed's cursor, so the next run takes only what's delivered after it. Returns
    every fixture payload's episode id, by its file stem.

    The starting wiki is committed as it is, lint error and all, as a wiki written before a
    rule was tightened is (`pa librarian seed` would refuse it).
    """
    ids = episode_ids()
    deliver_stage(root, "seed")
    data_root = DataRoot(root)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    write_wiki(root, ids, _starting_wiki())
    write_index(data_root.wiki)
    cursor = cursor_through(FilesystemL0(data_root.l0))
    repository.commit(f"Seed the page-creation fixture\n\n{CURSOR_FIELD}: {cursor}\n")
    return ids


def deliver_stage(root: Path, stage: str) -> None:
    """Ingests `stage`'s payloads into the data root at `root`, captured at its time."""
    data_root = DataRoot(root)
    for payload, inbox_name in _payloads(stage):
        target = data_root.inbox / inbox_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(payload, target)
    store = FilesystemL0(data_root.l0)
    data_root.catalog.parent.mkdir(parents=True, exist_ok=True)
    with open_catalog(data_root.catalog, store) as catalog:
        summary = ingest(data_root.inbox, store, catalog, OWNER, now=lambda: CAPTURED_AT[stage])
    assert summary.quarantined == 0, f"the {stage} stage has a malformed payload"


def write_wiki(root: Path, ids: dict[str, str], files: dict[str, str]) -> Path:
    """Writes `files` as the wiki of the data root at `root`, each `{ep:<payload>}` in them
    filled in with that payload's citation, and removes any other Markdown there."""
    wiki = DataRoot(root).wiki
    for stale in sorted(wiki.rglob("*.md")):
        if stale.relative_to(wiki).as_posix() not in files:
            stale.unlink()
    for path, text in files.items():
        (wiki / path).parent.mkdir(parents=True, exist_ok=True)
        cited = _PLACEHOLDER.sub(lambda m: f"[ep:{ids[m[1]]}]", text)
        (wiki / path).write_text(cited, encoding="utf-8")
    return wiki


def _payloads(stage: str) -> list[tuple[Path, PurePosixPath]]:
    """`stage`'s payloads, each with its name in the inbox (its source's folder, then it)."""
    directory = FIXTURE / stage
    return [
        (payload, PurePosixPath(payload.relative_to(directory).as_posix()))
        for payload in sorted(directory.rglob("*"))
        if payload.is_file()
    ]


def _starting_wiki() -> dict[str, str]:
    """The starting wiki's files, by path, their citations still naming payloads."""
    source = FIXTURE / "wiki"
    return {
        path.relative_to(source).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(source.rglob("*.md"))
    }


def _lint(root: Path) -> LintReport:
    data_root = DataRoot(root)
    return lint(
        data_root.wiki,
        l0=L0Metadata.of(FilesystemL0(data_root.l0).envelopes()),
        owner=OWNER,
        thresholds=Thresholds(today=RUN_CLOCK.date(), page_threshold=3, loop_age_days=30),
    )


def test_the_seeded_wiki_fails_lint_only_on_the_mailboxs_person_page(tmp_path: Path):
    seed_page_creation_fixture(tmp_path)

    report = _lint(tmp_path)

    assert [str(error) for error in report.errors] == [
        f"{MAILBOX_PAGE}:1: `emails` lists only automated mailboxes ({MAILBOX}): an automated "
        "mailbox never gets a person page; what it sends belongs on its organization's page or "
        "its topic's"
    ]
    assert report.warnings == ()


def test_the_run_after_the_seed_takes_exactly_the_night(tmp_path: Path):
    ids = seed_page_creation_fixture(tmp_path)
    data_root = DataRoot(tmp_path)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    deliver_stage(tmp_path, "night")

    batch = production_batch(
        FilesystemL0(data_root.l0), after=last_cursor(repository), time_zone=TIME_ZONE
    )

    assert batch is not None
    night = {payload.stem for payload, _ in _payloads("night")}
    assert len(night) == 10
    assert {e.episode_id for e in batch.episodes} == {ids[name] for name in night}
    # Three days, so a slug linked on each reaches the default page threshold.
    assert sorted({e.day.isoformat() for e in batch.episodes}) == [
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
    ]


def test_the_fixture_has_an_automated_mailbox_and_senders_the_owner_never_engaged_with(
    tmp_path: Path,
):
    ids = seed_page_creation_fixture(tmp_path)
    deliver_stage(tmp_path, "night")
    envelopes = list(FilesystemL0(DataRoot(tmp_path).l0).envelopes())

    table = identity_table(envelopes, OWNER)
    people = {person.slug: person for person in table.people}
    assert people[MAILBOX_SLUG].addresses == (MAILBOX,)
    assert people[MAILBOX_SLUG].automated
    assert people["lyle-brandt"].addresses == (COLD_SENDER,)
    assert not people["lyle-brandt"].automated
    assert people["priya-raman"].addresses == (FRIEND,)
    # Cedar Pine writes under its own name, so it is an organization and no one's person.
    assert {org.slug for org in table.organizations} == {
        "cedar-pine",
        "parcelpost",
        "brightpathsolar",
    }
    # The owner's own episodes are the mail to Priya Raman and the daily note: nothing the
    # owner sent or wrote goes to, or names, the mailbox, the cold sender, or Cedar Pine.
    engagers = L0Metadata.of(envelopes).engagers
    mine = {episode for episode, who in engagers.items() if OWNER.identifier in who}
    assert mine == {ids[name] for name in OWNERS_EPISODES}


def test_the_checks_find_everything_the_night_must_do_missing_from_the_starting_wiki(
    tmp_path: Path,
):
    ids = seed_page_creation_fixture(tmp_path)

    assert page_creation_problems(DataRoot(tmp_path).wiki, ids) == [
        f"{MAILBOX_PAGE} is a person page for the automated mailbox",
        "the open loop the mailbox's page held isn't on an organization's or a topic's page",
        "nothing the automated mailbox sent on the night is observed",
        "no open loop cites the request about the order the owner placed",
        "no person page lists Priya Raman's address",
    ]


PRIYA_PAGE = """\
---
type: person
aliases: [Priya]
status: active
summary: Friend, camping with us at Lake Wissota on Oct 24.
updated: 2026-10-14
emails: [priya.raman@example.org]
---
# Priya Raman

## Current state
Bringing her camp stove to Lake Wissota {ep:stove-reply}.

## Open loops
- [ ] me → [[priya-raman]]: the campsite number · asked 2026-10-13 · due 2026-10-16 {ep:stove-reply}

## Notes
"""
CEDAR_PINE_PAGE = """\
---
type: organization
aliases: []
status: active
summary: Outdoor shop; order #48213 is waiting on my choice of tent.
updated: 2026-10-14
domains: [cedarpine.example]
---
# Cedar Pine

## Current state
The camp chairs from order #48213 were delivered on Oct 14 {ep:parcel-delivered}.

## Open loops
- [ ] me → [[cedar-pine]]: gray tent or a refund · asked 2026-10-12 {ep:tent-order-problem}

## Notes
"""
PARCELPOST_PAGE = """\
---
type: organization
aliases: [ParcelPost Tracking]
status: active
summary: The carrier for my orders; owes me a replacement drill bit set by Oct 20.
updated: 2026-10-14
domains: [parcelpost.example]
---
# ParcelPost

## Current state
Delivered the camp chairs on Oct 14; still replacing the drill bit set {ep:parcel-delivered}.

## Open loops
- [ ] [[parcelpost]] → me: the replacement bit set · promised 2026-10-06 {ep:drill-delivered}

## Notes
Its updates come from a no-reply address, once filed as a person {ep:drill-shipped}.
"""
PARCELPOST_LOOP = (
    "- [ ] [[parcelpost]] → me: the replacement bit set · promised 2026-10-06 {ep:drill-delivered}"
)
"""The loop the mailbox's page held, as ParcelPost's page carries it."""
EXPECTED_WIKI = {
    # The seed's day logs, their links to the mailbox rewritten by the host after the merge.
    "days/2026-10-05.md": (
        "- [[parcelpost|parcelpost-tracking]] wrote that my cordless drill and drill bit set "
        "from [[lakeside-hardware]] shipped, due Oct 6 {ep:drill-shipped}\n"
    ),
    "days/2026-10-06.md": (
        "- [[parcelpost|parcelpost-tracking]] delivered the drill from [[lakeside-hardware]]; "
        "the drill bit set was damaged, and a replacement is promised by Oct 20 "
        "{ep:drill-delivered}\n"
    ),
    "days/2026-10-12.md": (
        "- [[cedar-pine]] asked me to choose a gray tent or a refund {ep:tent-order-problem}\n"
        "- [[parcelpost]] shipped the camp chairs from [[cedar-pine]] {ep:parcel-shipped}\n"
        "- [[lyle-brandt]] of [[brightpathsolar]] asked for a call {ep:solar-pitch}\n"
        "- I asked [[priya-raman]] to bring her camp stove {ep:stove-ask}\n"
    ),
    "days/2026-10-13.md": (
        "- [[parcelpost]] has the chairs at the depot {ep:parcel-out-for-delivery}\n"
        "- [[lyle-brandt]] asked again for a call {ep:solar-follow-up}\n"
        "- [[priya-raman]] asked me for the campsite number {ep:stove-reply}\n"
    ),
    "days/2026-10-14.md": (
        "- [[parcelpost]] delivered the chairs from [[cedar-pine]] {ep:parcel-delivered}\n"
        "- [[lyle-brandt]] asked once more for a call {ep:solar-last-note}\n"
        "- I owe [[priya-raman]] the campsite number {ep:2026-10-14_daily-note}\n"
    ),
    "people/priya-raman.md": PRIYA_PAGE,
    "orgs/cedar-pine.md": CEDAR_PINE_PAGE,
    "orgs/parcelpost.md": PARCELPOST_PAGE,
}
"""A wiki the night could leave under the page-creation rules, citing payloads by file stem."""

COLD_SENDER_PAGE = PRIYA_PAGE.replace("priya.raman@example.org", COLD_SENDER).replace(
    "- [ ] me → [[priya-raman]]: the campsite number · asked 2026-10-13 · due 2026-10-16 "
    "{ep:stove-reply}",
    "- [ ] me → [[lyle-brandt]]: a time for a call · asked 2026-10-12 {ep:solar-pitch}",
)

# What the old rules, or a rule read too broadly, would leave: a change to the expected wiki
# (a file's new text, or None to remove it), and the problems the checks must find in it.
DEFECTS: dict[str, tuple[dict[str, str | None], list[str]]] = {
    "the mailbox's person page still there": (
        {MAILBOX_PAGE: _starting_wiki()[MAILBOX_PAGE]},
        [f"{MAILBOX_PAGE} is a person page for the automated mailbox"],
    ),
    "the mailbox's person page kept, with its address taken off": (
        {MAILBOX_PAGE: _starting_wiki()[MAILBOX_PAGE].replace(MAILBOX, "")},
        [f"{MAILBOX_PAGE} is a person page for the automated mailbox"],
    ),
    "the mailbox's person page under another name": (
        {"people/parcelpost-updates.md": _starting_wiki()[MAILBOX_PAGE]},
        [
            "people/parcelpost-updates.md is a person page for the automated mailbox",
            f"people/parcelpost-updates.md still links [[{MAILBOX_SLUG}]], which has no page",
        ],
    ),
    "the loop the mailbox's page held dropped in the merge": (
        {"orgs/parcelpost.md": PARCELPOST_PAGE.replace(PARCELPOST_LOOP, "")},
        ["the open loop the mailbox's page held isn't on an organization's or a topic's page"],
    ),
    "the loop the mailbox's page held closed in the merge": (
        {
            "orgs/parcelpost.md": PARCELPOST_PAGE.replace(
                PARCELPOST_LOOP,
                PARCELPOST_LOOP.replace("- [ ]", "- [x]")
                + " · closed 2026-10-14 {ep:parcel-delivered}",
            )
        },
        ["the open loop the mailbox's page held isn't on an organization's or a topic's page"],
    ),
    "the loop the mailbox's page held moved to a person's page": (
        {
            "orgs/parcelpost.md": PARCELPOST_PAGE.replace(PARCELPOST_LOOP, ""),
            "people/priya-raman.md": PRIYA_PAGE.replace(
                "\n## Notes", f"{PARCELPOST_LOOP}\n\n## Notes"
            ),
        },
        ["the open loop the mailbox's page held isn't on an organization's or a topic's page"],
    ),
    "links to the mailbox's slug left without a page": (
        {
            "days/2026-10-05.md": EXPECTED_WIKI["days/2026-10-05.md"].replace(
                "[[parcelpost|parcelpost-tracking]]", "[[parcelpost-tracking]]"
            ),
            "orgs/parcelpost.md": PARCELPOST_PAGE.replace(
                "- [ ] [[parcelpost]] → me", "- [ ] [[parcelpost-tracking|ParcelPost]] → me"
            ),
        },
        [
            f"days/2026-10-05.md still links [[{MAILBOX_SLUG}]], which has no page",
            f"orgs/parcelpost.md still links [[{MAILBOX_SLUG}]], which has no page",
        ],
    ),
    "nothing kept of what the mailbox sent on the night": (
        {
            day: "".join(
                line for line in text.splitlines(keepends=True) if "[[parcelpost]]" not in line
            )
            for day, text in EXPECTED_WIKI.items()
            if day.startswith("days/")
        },
        ["nothing the automated mailbox sent on the night is observed"],
    ),
    "a page and a loop for the cold request": (
        {"people/lyle-brandt.md": COLD_SENDER_PAGE},
        [
            "people/lyle-brandt.md holds a loop a cold request opened (solar-pitch): "
            "a time for a call",
            "people/lyle-brandt.md is a page for a person the owner never engaged with",
        ],
    ),
    "no loop for the order": (
        {"orgs/cedar-pine.md": None},
        ["no open loop cites the request about the order the owner placed"],
    ),
    "the order's loop already closed": (
        {
            "orgs/cedar-pine.md": CEDAR_PINE_PAGE.replace("- [ ] me", "- [x] me").replace(
                "{ep:tent-order-problem}\n",
                "{ep:tent-order-problem} · closed 2026-10-14 {ep:parcel-delivered}\n",
            )
        },
        ["no open loop cites the request about the order the owner placed"],
    ),
    "no page for the friend the owner wrote to": (
        {"people/priya-raman.md": None},
        ["no person page lists Priya Raman's address"],
    ),
    "no loop for the friend's request": (
        {
            "people/priya-raman.md": PRIYA_PAGE.replace(
                "- [ ] me → [[priya-raman]]", "- [ ] me → me"
            )
        },
        ["no open loop with Priya Raman cites her request for the campsite number"],
    ),
}


def _expected_wiki(changes: dict[str, str | None]) -> dict[str, str]:
    """The expected wiki's files with `changes` made."""
    files = {**EXPECTED_WIKI, **changes}
    return {path: text for path, text in files.items() if text is not None}


def test_the_checks_pass_a_wiki_that_follows_the_rules_and_it_lints_clean(tmp_path: Path):
    ids = seed_page_creation_fixture(tmp_path)
    deliver_stage(tmp_path, "night")
    wiki = write_wiki(tmp_path, ids, EXPECTED_WIKI)

    assert page_creation_problems(wiki, ids) == []

    report = _lint(tmp_path)
    assert report.errors == ()
    # Lyle Brandt reached the threshold unengaged, so nothing asks for his page.
    assert report.warnings == ()


@pytest.mark.parametrize("defect", DEFECTS, ids=list(DEFECTS))
def test_the_checks_find_each_way_the_night_could_break_the_rules(tmp_path: Path, defect: str):
    changes, problems = DEFECTS[defect]
    ids = seed_page_creation_fixture(tmp_path)
    deliver_stage(tmp_path, "night")
    wiki = write_wiki(tmp_path, ids, _expected_wiki(changes))

    assert page_creation_problems(wiki, ids) == problems


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_LIBRARIAN") != "1",
    reason="runs the real librarian in its VM; set PA_SMOKE_LIBRARIAN=1 to run "
    "(deploy/home/README.md)",
)
def test_smoke_the_real_librarian_follows_the_page_creation_rules():
    """One real run in `pa-librarian`, as `deploy/home/librarian.sh run` makes it, over the
    night into the seeded wiki. The wiki is left in the target directory (under the replays'
    filesystem, which the VM may write) for reading; the run prints its cost."""
    target = _smoke_target()
    ids = seed_page_creation_fixture(target)
    config = load_librarian_config({"PA_LIBRARIAN_SANDBOX": str(DEPLOY / "librarian-sandbox.sh")})
    data_root = DataRoot(target)

    deliver_stage(target, "night")
    outcome = librarian.run(
        data_root, owner=OWNER, time_zone=TIME_ZONE, config=config, now=lambda: RUN_CLOCK
    )

    _report(outcome)
    problems = page_creation_problems(data_root.wiki, ids)
    assert not problems, f"in {data_root.wiki}:\n" + "\n".join(problems)


def page_creation_problems(wiki: Path, ids: dict[str, str]) -> list[str]:
    """How the wiki at `wiki` breaks the page-creation rules, or drops what they keep, after
    the night."""
    found = pages(wiki)
    people = [page for page in found if page.page_type is PERSON]
    loops = [(page, loop) for page in found for loop in page.loops]
    problems: list[str] = []

    # The automated mailbox: never a person. The page it had is merged away: its loop on an
    # organization's or a topic's page, and no link to its slug left without a page.
    problems += [
        f"{page.path} is a person page for the automated mailbox"
        for page in people
        if MAILBOX in _addresses(page) or MAILBOX_SLUG in _names(page)
    ]
    if not any(
        loop.is_open and ids[MAILBOX_LOOP] in loop.citations
        for page, loop in loops
        if page.page_type is not PERSON
    ):
        problems.append(
            "the open loop the mailbox's page held isn't on an organization's or a topic's page"
        )
    if MAILBOX_SLUG not in {page.slug for page in found}:
        problems += [
            f"{path.relative_to(wiki).as_posix()} still links [[{MAILBOX_SLUG}]], which has no page"
            for path in sorted(wiki.rglob("*.md"))
            if MAILBOX_SLUG in links(path.read_text(encoding="utf-8"))
        ]
    # What it sent on the night is still in the day logs.
    observed = {c for lines in day_logs(wiki).values() for line in lines for c in citations(line)}
    if not any(ids[name] in observed for name in PARCELS):
        problems.append("nothing the automated mailbox sent on the night is observed")

    # The cold request: no loop, and no page for a person the owner never engaged with.
    for page, loop in loops:
        opened_by = [name for name in COLD_REQUESTS if ids[name] in loop.citations]
        if opened_by:
            problems.append(
                f"{page.path} holds a loop a cold request opened ({', '.join(opened_by)}): "
                f"{loop.what}"
            )
    problems += [
        f"{page.path} is a page for a person the owner never engaged with"
        for page in people
        if COLD_SENDER in _addresses(page)
    ]

    # The order the owner placed: its request is a loop, though the owner never wrote there.
    if not any(loop.is_open and ids[ORDER_REQUEST] in loop.citations for _, loop in loops):
        problems.append("no open loop cites the request about the order the owner placed")

    # The control: the friend the owner wrote to has a page, and her request is a loop.
    friend = next((page for page in people if FRIEND in _addresses(page)), None)
    if friend is None:
        problems.append("no person page lists Priya Raman's address")
    elif not any(
        loop.is_open
        and friend.slug in (loop.owed_by, loop.owed_to)
        and any(ids[name] in loop.citations for name in FRIENDS_REQUEST)
        for _, loop in loops
    ):
        problems.append("no open loop with Priya Raman cites her request for the campsite number")
    return problems


def _addresses(page: Page) -> set[str]:
    return {address.strip().lower() for address in page.identifiers}


def _names(page: Page) -> set[str]:
    """Every name the page goes by, as slugs: its own, its title's, and its aliases'."""
    return {page.slug, slugify(page.title or ""), *(slugify(alias) for alias in page.aliases)}


def _smoke_target() -> Path:
    """A new directory for the run's data root, on the replays' filesystem (install-wiki.sh),
    the only place besides the live wiki that the librarian's VM may write a wiki."""
    configured = os.environ.get("PA_SMOKE_PAGE_CREATION_DIR", "").strip()
    if configured:
        target = Path(configured).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        target = REPLAYS / f"page-creation-{stamp}"
    assert not target.exists() or not any(target.iterdir()), f"{target} isn't empty"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _report(outcome: RunOutcome) -> None:
    sessions = ", ".join(
        f"{phase}: ${result.cost_usd or 0:.2f}, {result.turns} turns, {result.resumes} resumes"
        for phase, result in outcome.sessions
    )
    cost = sum(result.cost_usd or 0 for _, result in outcome.sessions)
    print(f"\nthe night: committed {outcome.commit}; ${cost:.2f} ({sessions})")
    for warning in outcome.warnings:
        print(f"  warning: {warning}")
