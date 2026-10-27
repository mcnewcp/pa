"""The corrections fixture (spec #56, stories 89 to 96): the owner correcting the wiki by chat.

The frozen corpora have no corrections, so this small fixture shows them. It is a data root
with a starting wiki and three stages of payloads, in `fixtures/corrections/`:

- `seed/`: mail and daily notes, already observed and folded into the starting wiki
  (`wiki/`, whose `{ep:<payload>}` citations name the seed's payloads by file stem). That wiki
  has Okie and Ben Okafor on two pages, two Chris Lees (an accountant and a soccer coach) on
  one, an open loop for a roofer's estimate, and a proposed Thanksgiving plan.
- `chats/`: the owner's chat episodes, saying Okie is Ben Okafor, the Chris Lees are two
  people, and the estimate came in, and answering a bare "yes" to the assistant's restatement
  of a changed Thanksgiving plan.
- `later/`: new mail and a note from both Chris Lees and from Okie, for a later run that must
  not merge the Chris Lees again.

The fast tests check the fixture itself. The real librarian's run over it is hand-run, since it
uses the real model.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import pytest

from pa_core.catalog import open_catalog
from pa_core.ingest import inbox_normalizer, ingest
from pa_core.librarian.batch import production_batch
from pa_core.librarian.lint import L0Metadata, Thresholds, lint
from pa_core.librarian.run import CURSOR_FIELD, RunOutcome, last_cursor
from pa_core.librarian.wiki import NOTES, Page, citations, day_logs, links, pages, slugify
from pa_core.owner import Owner
from pa_home import librarian
from pa_home.config import DataRoot, load_librarian_config
from pa_home.filesystem_l0 import FilesystemL0
from pa_home.git_wiki import GitWikiRepository

DEPLOY = Path(__file__).resolve().parents[1]
FIXTURE = DEPLOY / "tests" / "fixtures" / "corrections"
REPLAYS = Path.home() / ".local" / "share" / "pa-home-synthetic" / "replays"
"""PA_REPLAYS in lib.sh."""

OWNER = Owner(name="Argus McNevans", email_addresses=("argus@example.com",), other_names=("Gus",))
TIME_ZONE = ZoneInfo("America/Chicago")

STAGES = ("seed", "chats", "later")
CAPTURED_AT = {
    "seed": datetime(2026, 10, 12, 12, 0, tzinfo=TIME_ZONE),
    "chats": datetime(2026, 10, 14, 20, 0, tzinfo=TIME_ZONE),
    "later": datetime(2026, 10, 17, 12, 0, tzinfo=TIME_ZONE),
}
"""When each stage's mail and chats are captured (a daily note's capture time is its own)."""
RUN_CLOCKS = (
    datetime(2026, 10, 14, 21, 0, tzinfo=TIME_ZONE),
    datetime(2026, 10, 17, 21, 0, tzinfo=TIME_ZONE),
)
""""Now" for the run after the chats, and for the run after the later stage."""

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


def seed_corrections_fixture(root: Path) -> dict[str, str]:
    """Makes `root` a data root holding the seed's episodes and the starting wiki, committed
    with the seed's cursor, so the next run takes only what's delivered after it. Returns
    every fixture payload's episode id, by its file stem."""
    ids = episode_ids()
    deliver_stage(root, "seed")
    with tempfile.TemporaryDirectory(prefix="pa-corrections-") as directory:
        # Laid out as an eval wiki is, with the citations filled in, then seeded as one.
        seed = Path(directory)
        for source in sorted((FIXTURE / "wiki").rglob("*.md")):
            target = seed / "wiki" / source.relative_to(FIXTURE / "wiki")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = source.read_text(encoding="utf-8")
            target.write_text(
                _PLACEHOLDER.sub(lambda m: f"[ep:{ids[m[1]]}]", text), encoding="utf-8"
            )
        librarian.seed(DataRoot(root), seed, owner=OWNER, now=lambda: RUN_CLOCKS[0])
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


def _payloads(stage: str) -> list[tuple[Path, PurePosixPath]]:
    """`stage`'s payloads, each with its name in the inbox (its source's folder, then it)."""
    directory = FIXTURE / stage
    return [
        (payload, PurePosixPath(payload.relative_to(directory).as_posix()))
        for payload in sorted(directory.rglob("*"))
        if payload.is_file()
    ]


def test_the_seeded_wiki_lints_clean_and_the_next_run_takes_exactly_the_chats(tmp_path: Path):
    ids = seed_corrections_fixture(tmp_path)
    data_root = DataRoot(tmp_path)
    store = FilesystemL0(data_root.l0)

    report = lint(
        data_root.wiki,
        l0=L0Metadata.of(store.envelopes()),
        owner=OWNER,
        thresholds=Thresholds(today=RUN_CLOCKS[0].date(), page_threshold=3, loop_age_days=30),
    )
    assert report.errors == ()

    deliver_stage(tmp_path, "chats")
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    batch = production_batch(store, after=last_cursor(repository), time_zone=TIME_ZONE)

    assert batch is not None
    assert {e.episode_id for e in batch.episodes} == {
        ids["okie-is-ben"],
        ids["two-chris-lees"],
        ids["roofing-estimate-came"],
        ids["thanksgiving-change"],
        ids["thanksgiving-yes"],
    }


def test_the_run_after_the_chats_run_takes_exactly_the_later_stage(tmp_path: Path):
    ids = seed_corrections_fixture(tmp_path)
    data_root = DataRoot(tmp_path)
    store = FilesystemL0(data_root.l0)
    repository = GitWikiRepository.open(data_root.wiki, data_root.wiki_git)
    deliver_stage(tmp_path, "chats")
    chats = production_batch(store, after=last_cursor(repository), time_zone=TIME_ZONE)
    assert chats is not None
    repository.commit(f"The chats run\n\n{CURSOR_FIELD}: {chats.cursor}\n")

    deliver_stage(tmp_path, "later")
    batch = production_batch(store, after=last_cursor(repository), time_zone=TIME_ZONE)

    assert batch is not None
    assert {e.episode_id for e in batch.episodes} == {
        ids["practice-moved"],
        ids["statement-received"],
        ids["2026-10-16_daily-note"],
    }


def test_the_checks_find_every_correction_missing_from_the_starting_wiki(tmp_path: Path):
    ids = seed_corrections_fixture(tmp_path)

    assert corrections_problems(DataRoot(tmp_path).wiki, ids) == [
        "people/ben-okafor.md doesn't go by both Okie and Ben Okafor: ['ben', 'ben-okafor']",
        "people/ben-okafor.md doesn't cite the chat saying Okie is Ben Okafor",
        "people/ben-okafor.md doesn't carry Okie's loop for the gutter guy's number",
        "people/okie.md is still a page of its own",
        "days/2026-10-04.md still links [[okie]]",
        "days/2026-10-08.md still links [[okie]]",
        "index.md still links [[okie]]",
        "people/okie.md still links [[okie]]",
        "people/chris-lee.md still holds both Chris Lees",
        "Summit Roofing's estimate loop isn't closed citing the chat",
        "no current decision for Thanksgiving in Duluth cites the owner's yes",
        "hosting Thanksgiving is still a current decision",
    ]


def test_the_checks_read_links_as_the_wiki_does_piped_or_not(tmp_path: Path):
    ids = seed_corrections_fixture(tmp_path)
    wiki = DataRoot(tmp_path).wiki
    # The host's rewrite goes to Ben Okafor's page, still showing "okie"; a piped link to
    # okie still links okie.
    for day, old, new in (
        ("2026-10-04", "[[okie]]", "[[ben-okafor|okie]]"),
        ("2026-10-08", "[[okie]]", "[[okie|Okie]]"),
    ):
        log = wiki / "days" / f"{day}.md"
        assert old in log.read_text()
        log.write_text(log.read_text().replace(old, new))

    problems = corrections_problems(wiki, ids)

    assert "days/2026-10-04.md still links [[okie]]" not in problems
    assert "days/2026-10-08.md still links [[okie]]" in problems


@pytest.mark.skipif(
    os.environ.get("PA_SMOKE_LIBRARIAN") != "1",
    reason="runs the real librarian in its VM; set PA_SMOKE_LIBRARIAN=1 to run "
    "(deploy/home/README.md)",
)
def test_smoke_the_real_librarian_applies_the_owners_corrections_from_chat():
    """Two real runs in `pa-librarian`, as `deploy/home/librarian.sh run` makes them: one after
    the chats, then one after the later stage. The wiki is left in the target directory (under
    the replays' filesystem, which the VM may write) for reading; the run prints its cost."""
    target = _smoke_target()
    ids = seed_corrections_fixture(target)
    config = load_librarian_config({"PA_LIBRARIAN_SANDBOX": str(DEPLOY / "librarian-sandbox.sh")})
    data_root = DataRoot(target)
    problems: list[str] = []

    deliver_stage(target, "chats")
    first = librarian.run(
        data_root, owner=OWNER, time_zone=TIME_ZONE, config=config, now=lambda: RUN_CLOCKS[0]
    )
    _report("after the chats", first)
    problems += [f"after the chats: {p}" for p in corrections_problems(data_root.wiki, ids)]

    deliver_stage(target, "later")
    second = librarian.run(
        data_root, owner=OWNER, time_zone=TIME_ZONE, config=config, now=lambda: RUN_CLOCKS[1]
    )
    _report("after the later stage", second)
    problems += [f"after the later stage: {p}" for p in later_problems(data_root.wiki, ids)]

    assert not problems, f"in {data_root.wiki}:\n" + "\n".join(problems)


ACCOUNTANT = "chris@leetaxgroup.example"
COACH = "chrislee.coach@gmail.com"


def corrections_problems(wiki: Path, ids: dict[str, str]) -> list[str]:
    """How the wiki at `wiki` falls short of the owner's corrections in the chats stage."""
    found = {page.slug: page for page in pages(wiki)}
    text = {path: path.read_text(encoding="utf-8") for path in sorted(wiki.rglob("*.md"))}
    problems: list[str] = []

    # The merge: one page, both names, links rewritten, the chat cited.
    ben = _page_with_address(found, "ben.okafor@example.com")
    if ben is None:
        problems.append("no page lists Ben Okafor's address")
    else:
        names = {ben.slug, *(slugify(alias) for alias in ben.aliases)}
        if not {"okie", "ben-okafor"} <= names:
            problems.append(f"{ben.path} doesn't go by both Okie and Ben Okafor: {sorted(names)}")
        if ids["okie-is-ben"] not in _cited(ben):
            problems.append(f"{ben.path} doesn't cite the chat saying Okie is Ben Okafor")
        if not any(loop.owed_by == ben.slug and "gutter" in loop.what for loop in ben.loops):
            problems.append(f"{ben.path} doesn't carry Okie's loop for the gutter guy's number")
    if "okie" in found and (ben is None or ben.slug != "okie"):
        problems.append("people/okie.md is still a page of its own")
    problems += [
        f"{p.relative_to(wiki)} still links [[okie]]"
        for p, t in text.items()
        if "okie" in links(t) and (ben is None or ben.slug != "okie")
    ]

    # The split: two pages, each saying so with the chat's citation.
    problems += _split_problems(found, ids)

    # The loop the chat closed.
    estimate = [
        loop
        for page in found.values()
        for loop in page.loops
        if loop.owed_by == "summit-roofing" and "estimate" in loop.what
    ]
    if not estimate:
        problems.append("Summit Roofing's estimate loop is gone")
    elif not any(
        loop.closed and ids["roofing-estimate-came"] in loop.closed.citations for loop in estimate
    ):
        problems.append("Summit Roofing's estimate loop isn't closed citing the chat")

    # The bare "yes": the restated plan applied, citing the owner's "yes".
    decisions = [d for page in found.values() for d in page.decisions if d.superseded is None]
    if not any("Duluth" in d.choice and ids["thanksgiving-yes"] in d.citations for d in decisions):
        problems.append("no current decision for Thanksgiving in Duluth cites the owner's yes")
    if any("host" in d.choice and d.stance != "rejected" for d in decisions):
        problems.append("hosting Thanksgiving is still a current decision")

    # The assistant's words are never memory.
    problems += [
        f"{p.relative_to(wiki)} took the assistant's word about Denver"
        for p, t in text.items()
        if "Denver" in t
    ]
    return problems


def later_problems(wiki: Path, ids: dict[str, str]) -> list[str]:
    """How the wiki at `wiki` lets the corrections slip after the later stage's new mentions."""
    found = {page.slug: page for page in pages(wiki)}
    problems = _split_problems(found, ids)
    accountant = _page_with_address(found, ACCOUNTANT)
    coach = _page_with_address(found, COACH)
    logged = {
        citation: line
        for lines in day_logs(wiki).values()
        for line in lines
        for citation in citations(line)
    }
    for name, page, other in (
        ("statement-received", accountant, coach),
        ("practice-moved", coach, accountant),
    ):
        line = logged.get(ids[name], "")
        if page and page.slug not in links(line):
            problems.append(f"the observation of {name} doesn't link [[{page.slug}]]: {line!r}")
        if other and page and other.slug != page.slug and other.slug in links(line):
            problems.append(f"the observation of {name} links the other Chris Lee: {line!r}")
    for path in sorted(wiki.rglob("*.md")):
        if "okie" in links(path.read_text(encoding="utf-8")) and "okie" not in found:
            problems.append(f"{path.relative_to(wiki)} links [[okie]] again")
    return problems


def _split_problems(found: dict[str, Page], ids: dict[str, str]) -> list[str]:
    accountant = _page_with_address(found, ACCOUNTANT)
    coach = _page_with_address(found, COACH)
    if accountant is None or coach is None:
        return [f"no page lists {ACCOUNTANT if accountant is None else COACH}"]
    if accountant.slug == coach.slug:
        return [f"{accountant.path} still holds both Chris Lees"]
    problems = [
        f"{page.path} doesn't note the split, citing the chat"
        for page in (accountant, coach)
        if not any(ids["two-chris-lees"] in citations(line) for line in _lines(page, NOTES))
    ]
    if not any("property tax" in loop.what for loop in accountant.loops):
        problems.append(f"{accountant.path} lost the accountant's property tax loop")
    if any("property tax" in loop.what for loop in coach.loops):
        problems.append(f"{coach.path} holds the accountant's property tax loop")
    return problems


def _page_with_address(found: dict[str, Page], address: str) -> Page | None:
    return next((page for page in found.values() if address in page.fields.get("emails", ())), None)


def _lines(page: Page, section: str) -> list[str]:
    found = page.section(section)
    return [line for _, line in found.lines] if found else []


def _cited(page: Page) -> set[str]:
    return {c for section in page.sections for _, line in section.lines for c in citations(line)}


def _smoke_target() -> Path:
    """A new directory for the run's data root, on the replays' filesystem (install-wiki.sh),
    the only place besides the live wiki that the librarian's VM may write a wiki."""
    configured = os.environ.get("PA_SMOKE_CORRECTIONS_DIR", "").strip()
    if configured:
        target = Path(configured).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        target = REPLAYS / f"corrections-{stamp}"
    assert not target.exists() or not any(target.iterdir()), f"{target} isn't empty"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _report(when: str, outcome: RunOutcome) -> None:
    sessions = ", ".join(
        f"{phase}: ${result.cost_usd or 0:.2f}, {result.turns} turns, {result.resumes} resumes"
        for phase, result in outcome.sessions
    )
    cost = sum(result.cost_usd or 0 for _, result in outcome.sessions)
    print(f"\n{when}: committed {outcome.commit}; ${cost:.2f} ({sessions})")
    for warning in outcome.warnings:
        print(f"  warning: {warning}")
