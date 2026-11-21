"""Lint: what may be committed to the wiki.

An error fails the run. The rules so far:

- **Layout.** Nothing but `index.md`, the root files (`proposals.md` and `retired.md`), and
  Markdown files directly in `days/`, `people/`, `orgs/`, and `topics/`: no dotfiles, symbolic
  links, subdirectories, other files, or any `CLAUDE.md`, so nothing in the wiki can be loaded
  as instructions or run as code (ADR-0007).
  A day log is named for its day (`YYYY-MM-DD.md`), a page for its slug (`<slug>.md`).
- **Day logs** hold only observation lines (and blank lines).
- **Pages** follow their type's schema (`wiki.py`): frontmatter with exactly the type's fields
  and valid values; the type's sections, in order, with only a `# <title>` before them;
  well-formed decision and loop lines in Decisions and Open loops; a citation on every other
  line; and no template `<placeholder>` left in its summary, aliases, or title. No page is
  named for a file at the wiki's top level (`index`, `proposals`, `retired`), which a link by
  that name goes to.
- **Citations** name episodes that are in L0.
- **Proposals.** `proposals.md` holds its two sections, `## Open` and `## Answered`, after
  at most its title, `# Proposals`, and nothing in them but well-formed proposal lines, open
  ones under Open and answered ones under Answered. No two share an id; a proposal's text is
  short and holds no URL; every link of an open proposal goes to a page, or to a retired
  slug; and an answered proposal's last citation is one of the owner's chat exchanges, so
  nothing but the owner's word closes one.
- **Retired slugs.** `retired.md` holds nothing but retirement lines, each slug once, after an
  optional title.
- **Identifiers.** A person page's `emails` and an organization page's `domains` are in L0's
  metadata (any episode's participants), no address or domain is on two pages, and no page
  lists one of the owner's configured addresses (the owner is `me`). A person page's addresses
  aren't all automated mailboxes (`identity.py`): a machine never gets a person page.

A warning doesn't fail the run; it's recorded with it, and handed to the next consolidation:

- **Orphan pages**, which nothing in the wiki links to.
- **Aging loops**: open past their due date, or open longer than the loop age (30 days unless
  configured otherwise).
- **Duplicate candidates**: pages sharing an alias, a page with an alias that's another page's
  slug or name, or pages with near-identical slugs.
- **Slugs without pages**: linked on enough days to reach the page threshold, and engaged
  (`engagement.py`), but with no page. A retired slug isn't one until the owner engages with
  it again (`retirement.py`).
- **"Needs lookup" markers** for outside information, until the research worker exists.

Each rule is a function over the wiki's files that yields findings; add a rule by writing one
and listing it in `_RULES` (rules about the layout itself live in `_layout`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

from pa_core.envelope import Envelope, Kind
from pa_core.librarian.engagement import engaged_days, engagers
from pa_core.librarian.identity import is_automated_mailbox
from pa_core.librarian.links import alias_targets
from pa_core.librarian.retirement import standing
from pa_core.librarian.wiki import (
    ANSWERED,
    DAYS,
    DECISION_FORMAT,
    DECISIONS,
    FOLDERS,
    INDEX,
    LOOP_FORMAT,
    NEEDS_LOOKUP,
    OPEN,
    OPEN_LOOPS,
    ORGANIZATION,
    PAGE_TYPES,
    PERSON,
    PLACEHOLDER,
    PROPOSAL_FORMAT,
    PROPOSAL_TEXT_LIMIT,
    PROPOSALS,
    PROPOSALS_TITLE,
    RETIRED,
    RETIREMENT_FORMAT,
    ROOT_FILES,
    FieldValue,
    Page,
    PageFormatError,
    PageType,
    citations,
    day_of,
    is_page_name,
    linked_days,
    links,
    pages,
    parse_decision,
    parse_loop,
    parse_observation,
    parse_proposal,
    parse_retirement,
    read_page,
    read_retired,
    slugify,
)
from pa_core.owner import Owner


class WarningKind(StrEnum):
    """What a warning is about. Dreaming judges each warning as a candidate of its kind
    (`pa_core.librarian.candidates`)."""

    ORPHAN = "orphan"
    LOOP_PAST_DUE = "loop-past-due"
    LOOP_AGING = "loop-aging"
    """A loop open longer than the loop age, and not past due."""
    DUPLICATE = "duplicate"
    SLUG_WITHOUT_PAGE = "slug-without-page"
    NEEDS_LOOKUP = "needs-lookup"


@dataclass(frozen=True)
class Finding:
    """One problem lint found, where it is in the wiki."""

    path: str
    """The file, relative to the wiki."""
    message: str
    line: int | None = None
    """The line, counting from 1, when the problem is on one."""
    kind: WarningKind | None = None
    """A warning's kind; an error has none."""
    pages: tuple[str, ...] = ()
    """The pages a warning is about, relative to the wiki: both pages of a duplicate."""
    episodes: tuple[str, ...] = ()
    """For a warning about a loop, the episodes its line cites as opening it: which loop it is,
    wherever its line moves."""
    slug: str | None = None
    """For a slug without a page, the slug."""

    def __str__(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class LintReport:
    errors: tuple[Finding, ...]
    """Problems that fail the run."""
    warnings: tuple[Finding, ...] = ()
    """Problems that don't: recorded with the run, and handed to the next consolidation."""
    engaged_days: Mapping[str, Sequence[date]] = field(default_factory=dict)
    """For each slug the owner has engaged with, the days that show it (`engaged_days` in
    `engagement.py`): what the warnings went by, for whoever tells a session the same."""


@dataclass(frozen=True)
class L0Metadata:
    """What lint checks the wiki's evidence and identifiers against: L0's episodes and the
    addresses of their participants, every episode included, even those never offered."""

    episode_ids: frozenset[str]
    addresses: frozenset[str]
    """Every participant's address, in lowercase."""
    engagers: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """For each episode that is someone's own doing, the addresses it's the doing of, in
    lowercase (`engagers` in `engagement.py`): what engagement is read from."""
    chats: frozenset[str] = frozenset()
    """The episodes that are chat exchanges with the assistant."""

    @classmethod
    def of(cls, envelopes: Iterable[Envelope]) -> L0Metadata:
        ids: set[str] = set()
        addresses: set[str] = set()
        doers: dict[str, frozenset[str]] = {}
        chats: set[str] = set()
        for envelope in envelopes:
            ids.add(envelope.episode_id)
            addresses.update(
                p.identifier.strip().lower() for p in envelope.participants if "@" in p.identifier
            )
            if who := engagers(envelope):
                doers[envelope.episode_id] = who
            if envelope.kind is Kind.CHAT:
                chats.add(envelope.episode_id)
        return cls(frozenset(ids), frozenset(addresses), doers, frozenset(chats))

    def owner_chats(self, owner: Owner) -> frozenset[str]:
        """The chat exchanges `owner` took part in: where the owner's own word is."""
        mine = {address.strip().lower() for address in owner.email_addresses}
        return frozenset(
            episode
            for episode in self.chats
            if not self.engagers.get(episode, frozenset()).isdisjoint(mine)
        )

    @property
    def domains(self) -> frozenset[str]:
        """The mail domain of every address, as seen (subdomains included)."""
        return frozenset(a.rpartition("@")[2] for a in self.addresses)


@dataclass(frozen=True)
class WikiFile:
    """A Markdown file in the wiki's layout, and its lines."""

    path: str
    """Relative to the wiki, such as `days/2026-10-05.md`."""
    lines: tuple[str, ...]

    @property
    def is_day_log(self) -> bool:
        return self.path.startswith(f"{DAYS}/")


type Rule = Callable[[WikiFile, L0Metadata], Iterator[Finding]]
"""A check of one file, given L0's metadata."""


STALE_LATER_DAYS = 3
"""Unless configured otherwise, the days a page is linked on after its last update before
dreaming is offered it as stale."""
QUIET_DAYS = 90
"""Unless configured otherwise, how long an active page may go unlinked before dreaming is
offered it as quiet."""


@dataclass(frozen=True)
class Thresholds:
    """What the warnings, and dreaming's other candidates, measure against."""

    today: date
    """The day the run is told it is, in the owner's time zone."""
    page_threshold: int
    """The distinct days a slug is linked on before it should have a page."""
    loop_age_days: int
    """How long a loop may stay open before it's flagged."""
    stale_later_days: int = STALE_LATER_DAYS
    """The distinct days, after a page's `updated` date and up to `today`, that observations
    link the page's slug on before the page is flagged as stale. Lint has no warning for it;
    it is a candidate for dreaming (`pa_core.librarian.candidates`)."""
    quiet_days: int = QUIET_DAYS
    """The days since an observation last linked an active page's slug, counting none after
    `today`, at which the page is flagged as quiet. A candidate for dreaming too."""


def lint(tree: Path, *, l0: L0Metadata, owner: Owner, thresholds: Thresholds) -> LintReport:
    """Lints the wiki at `tree` against `l0`, the metadata of every episode in L0, for
    `owner`."""
    files: list[WikiFile] = []
    errors = list(_layout(tree, files))
    for file in files:
        for rule in _RULES:
            errors.extend(rule(file, l0))
    errors.extend(_proposals(files, tree, l0, owner))
    errors.extend(_retired(files))
    found = pages(tree)
    errors.extend(_identifiers(found, l0, owner))
    engaged = engaged_days(tree, l0, owner)
    warnings = [
        *_orphans(files, found),
        *_aging_loops(found, thresholds),
        *_duplicate_candidates(found),
        *_slugs_without_pages(
            tree, found, thresholds, engaged, standing(read_retired(tree), engaged)
        ),
        *_needs_lookup(files),
    ]
    return LintReport(errors=tuple(errors), warnings=tuple(warnings), engaged_days=engaged)


_NOT_IN_LAYOUT = (
    f"isn't part of the wiki's layout: only {', '.join((INDEX, *ROOT_FILES))}, and Markdown in "
    "days/, people/, orgs/, and topics/"
)


def _layout(tree: Path, files: list[WikiFile]) -> Iterator[Finding]:
    """Findings about anything outside the layout; the files inside it go into `files`."""
    for entry in sorted(tree.iterdir()):
        if entry.name in (INDEX, *ROOT_FILES) and entry.is_file() and not entry.is_symlink():
            yield from _read(entry, entry.name, files)
        elif entry.name in FOLDERS and entry.is_dir() and not entry.is_symlink():
            yield from _folder(entry, files)
        else:
            yield _misplaced(entry, entry.name)


def _folder(folder: Path, files: list[WikiFile]) -> Iterator[Finding]:
    for entry in sorted(folder.iterdir()):
        path = f"{folder.name}/{entry.name}"
        if entry.is_symlink() or not entry.is_file():
            yield _misplaced(entry, path)
        elif entry.name.lower() == "claude.md":
            yield Finding(path, "a CLAUDE.md is never allowed in the wiki")
        elif folder.name == DAYS and day_of(entry.name) is None:
            yield Finding(path, "isn't a day log: day logs are named YYYY-MM-DD.md")
        elif folder.name != DAYS and not is_page_name(entry.name):
            yield Finding(path, "isn't a page: pages are named <slug>.md")
        else:
            yield from _read(entry, path, files)


def _misplaced(entry: Path, path: str) -> Finding:
    if entry.is_symlink():
        return Finding(path, "is a symbolic link; the wiki holds only files")
    if entry.name.lower() == "claude.md":
        return Finding(path, "a CLAUDE.md is never allowed in the wiki")
    if entry.is_dir() and path.count("/"):
        return Finding(path, "is a directory; the wiki's folders hold only Markdown files")
    return Finding(path, _NOT_IN_LAYOUT)


def _read(entry: Path, path: str, files: list[WikiFile]) -> Iterator[Finding]:
    try:
        text = entry.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        yield Finding(path, "isn't UTF-8 text")
        return
    files.append(WikiFile(path, tuple(text.splitlines())))


def _observations(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """A day log holds only observation lines."""
    if not file.is_day_log:
        return
    for number, line in enumerate(file.lines, start=1):
        if line.strip() and parse_observation(line) is None:
            yield Finding(
                file.path,
                "malformed observation: expected `- <one line linking a [[slug]]> "
                f"[ep:<episode_id>]`, got {line!r}",
                number,
            )


def _cited_episodes_exist(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """Every citation names an episode in L0."""
    for number, line in enumerate(file.lines, start=1):
        for episode_id in citations(line):
            if episode_id not in l0.episode_ids:
                yield Finding(file.path, f"cites {episode_id}, which isn't in L0", number)


def _pages(file: WikiFile, l0: L0Metadata) -> Iterator[Finding]:
    """A page follows its type's schema: frontmatter, sections, and line formats."""
    folder = file.path.partition("/")[0]
    if folder not in PAGE_TYPES:
        return
    slug = file.path.rpartition("/")[2].removesuffix(".md")
    if f"{slug}.md" in (INDEX, *ROOT_FILES):
        yield Finding(
            file.path,
            f"a page can't be named `{slug}`: {slug}.md is a file at the wiki's top level, and "
            f"[[{slug}]] links it",
        )
    try:
        page = read_page(file.path, "\n".join(file.lines))
    except PageFormatError as error:
        yield Finding(file.path, f"bad frontmatter: {error.message}", error.line)
        return
    page_type = PAGE_TYPES[folder]
    for problem in _frontmatter_problems(page.fields, page_type):
        yield Finding(file.path, f"bad frontmatter: {problem}", 1)
    yield from _sections(page, page_type)
    yield from _section_lines(page)


def _frontmatter_problems(fields: Mapping[str, FieldValue], page_type: PageType) -> Iterator[str]:
    identifiers = page_type.list_field
    expected = page_type.fields
    for key in expected:
        if key not in fields:
            yield f"`{key}` is missing"
    for key in fields:
        if key not in expected:
            yield f"`{key}` isn't a field of a {page_type.type} page"
    if "type" in fields and fields["type"] != page_type.type:
        yield (
            f"`type` is {fields['type']!r}, but pages in {page_type.folder}/ are "
            f"{page_type.type} pages"
        )
    statuses = page_type.statuses
    if "status" in fields and fields["status"] not in statuses:
        yield f"`status` is {fields['status']!r}, not one of {', '.join(statuses)}"
    summary = fields.get("summary")
    if "summary" in fields and not (isinstance(summary, str) and summary):
        yield "`summary` must be one line of text"
    if isinstance(summary, str) and (placeholder := PLACEHOLDER.search(summary)):
        yield f"`summary` still holds the template's placeholder {placeholder[0]}"
    aliases = fields.get("aliases")
    for alias in aliases if isinstance(aliases, tuple) else ():
        if placeholder := PLACEHOLDER.search(alias):
            yield f"`aliases` still holds the template's placeholder {placeholder[0]}"
    updated = fields.get("updated")
    if "updated" in fields and not (isinstance(updated, str) and _is_date(updated)):
        yield f"`updated` must be a date, YYYY-MM-DD, not {updated!r}"
    for key in ("aliases", *([identifiers] if identifiers else [])):
        if key in fields and not isinstance(fields[key], tuple):
            yield f"`{key}` must be a list, such as [a, b] or []"


def _sections(page: Page, page_type: PageType) -> Iterator[Finding]:
    expected = page_type.sections
    names = tuple(section.name for section in page.sections)
    if names != expected:
        line = page.sections[0].line if page.sections else None
        yield Finding(
            page.path,
            f"wrong sections for a {page_type.type} page: expected "
            f"{', '.join(f'`## {name}`' for name in expected)}, in that order; got "
            f"{', '.join(f'`## {name}`' for name in names) or 'none'}",
            line,
        )
    for number, line in page.preamble:
        if number != page.preamble[0][0] or not line.startswith("# "):
            yield Finding(
                page.path,
                f"text before its first section: only a `# <title>` line may come before "
                f"the sections, got {line!r}",
                number,
            )
        elif placeholder := PLACEHOLDER.search(line):
            yield Finding(
                page.path,
                f"its title still holds the template's placeholder {placeholder[0]}",
                number,
            )


def _section_lines(page: Page) -> Iterator[Finding]:
    for section in page.sections:
        for number, line in section.lines:
            if section.name == DECISIONS and parse_decision(line) is None:
                yield Finding(
                    page.path,
                    f"malformed decision: expected `{DECISION_FORMAT}`, got {line!r}",
                    number,
                )
            elif section.name == OPEN_LOOPS and parse_loop(line) is None:
                yield Finding(
                    page.path, f"malformed loop: expected `{LOOP_FORMAT}`, got {line!r}", number
                )
            elif (
                section.name not in (DECISIONS, OPEN_LOOPS)
                and not line.startswith("#")
                and not citations(line)
            ):
                yield Finding(
                    page.path,
                    "a line without a citation: every line cites the episodes it came from, as "
                    f"[ep:<episode_id>]; got {line!r}",
                    number,
                )


def _identifiers(pages: Iterable[Page], l0: L0Metadata, owner: Owner) -> Iterator[Finding]:
    """A page's addresses and domains are in L0's metadata, none is on two pages, none of
    its addresses is the owner's, and a person's aren't all automated mailboxes."""
    known = {PERSON: l0.addresses, ORGANIZATION: l0.domains}
    owners = {address.strip().lower() for address in owner.email_addresses}
    listed: dict[tuple[str, str], str] = {}
    """The page listing each identifier first, by its field and its lowercase form."""
    for page in pages:
        page_type = page.page_type
        key = page_type.list_field
        for value in page.identifiers:
            identifier = value.strip().lower()
            if identifier not in known.get(page_type, frozenset()):
                yield Finding(
                    page.path,
                    f"`{key}` lists {value}, which isn't in L0's metadata: identifiers are "
                    "copied from the identity table, never made up",
                    1,
                )
            if page_type is PERSON and identifier in owners:
                yield Finding(
                    page.path,
                    f"`{key}` lists {value}, one of the owner's addresses: the owner is `me`, "
                    "never on a page",
                    1,
                )
            first = listed.setdefault((str(key), identifier), page.path)
            if first != page.path:
                yield Finding(
                    page.path,
                    f"`{key}` lists {value}, which {first} lists too: {page_type.identifier} "
                    "belongs on one page",
                    1,
                )
        if (
            page_type is PERSON
            and page.identifiers
            and all(is_automated_mailbox(value) for value in page.identifiers)
        ):
            yield Finding(
                page.path,
                f"`{key}` lists only automated mailboxes ({', '.join(page.identifiers)}): an "
                "automated mailbox never gets a person page; what it sends belongs on its "
                "organization's page or its topic's",
                1,
            )


_URL = re.compile(
    r"[a-z][a-z0-9+.-]*://\S*"  # anything with a scheme
    r"|\bwww\.\S+"
    r"|\bmailto:\S+"
    r"|\]\(\S*"  # a Markdown link's target
    r"|[^\s@]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+"  # an email address
    # A bare hostname, with its path if it has one: a dotted name ending in letters.
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b(?:/\S*)?",
    re.IGNORECASE,
)
"""A web address, or anything a reader's app would turn into one: a chat client on a phone
links a bare hostname and an email address. A slug holds no dot, so no link to a page is one.
An abbreviation (`e.g.`, `a.m.`, `U.S.`) and a figure (`2.5`) aren't either: a hostname's last
part is two letters or more. Prose that leaves out the space after a full stop is taken for
one, which costs its session a resume."""


def _proposals(
    files: Sequence[WikiFile], tree: Path, l0: L0Metadata, owner: Owner
) -> Iterator[Finding]:
    """`proposals.md` follows its schema, every line of it: the two sections, in order, with
    only its fixed title before them, and in them only proposal lines, each in its section,
    with its own id, short plain text, and, while it's open, links that go to pages. An
    answered one cites one of `owner`'s chat exchanges last: only the owner's word closes a
    proposal.

    A proposal reaches the owner in the librarian's voice, so its text can't carry a link or a
    paragraph of someone else's. A link goes to a page when its slug is a page's, or an alias
    the host is about to rewrite to its page's (`links.py`), or a retired slug (`retired.md`
    lists it): the page a proposal is about may be retired while it waits for its answer. An
    answered proposal is history:
    its links stay as written, whatever became of the pages.
    """
    file = next((file for file in files if file.path == PROPOSALS), None)
    if file is None:
        return
    resolvable = {page.slug for page in pages(tree)} | alias_targets(tree).keys()
    # A retired slug's link stays good: the proposal may be what brings its page back.
    resolvable |= read_retired(tree).keys()
    owners_word = l0.owner_chats(owner)
    expected = (OPEN, ANSWERED)
    headings: list[tuple[int, str]] = []
    titled = False
    first: dict[str, int] = {}
    """The line of the first proposal with each id."""
    for number, line in enumerate(file.lines, start=1):
        if not line.strip():
            continue
        if line.startswith("## "):
            headings.append((number, line.removeprefix("## ").strip()))
            continue
        if not headings:
            if titled or not line.startswith("# "):
                yield Finding(
                    PROPOSALS,
                    f"text before its first section: only its title, `{PROPOSALS_TITLE}`, may "
                    f"come before the sections, got {line!r}",
                    number,
                )
            elif line.rstrip() != PROPOSALS_TITLE:
                # The owner reads the file whole, so no line of it is free text.
                yield Finding(
                    PROPOSALS,
                    f"its title is `{PROPOSALS_TITLE}`, and nothing else, got {line!r}",
                    number,
                )
            titled = True
            continue
        proposal = parse_proposal(line)
        if proposal is None:
            yield Finding(
                PROPOSALS, f"malformed proposal: expected `{PROPOSAL_FORMAT}`, got {line!r}", number
            )
            continue
        name = proposal.id
        section = headings[-1][1]
        if section in expected and proposal.is_open != (section == OPEN):
            state, belongs = ("open", OPEN) if proposal.is_open else ("answered", ANSWERED)
            yield Finding(
                PROPOSALS,
                f"{name} is {state}, under `## {section}`: {state} proposals are listed under "
                f"`## {belongs}`",
                number,
            )
        if name in first:
            yield Finding(
                PROPOSALS,
                f"{name} is already the id of the proposal on line {first[name]}: every "
                "proposal has an id of its own, and an id is never reused",
                number,
            )
        first.setdefault(name, number)
        if len(proposal.text) > PROPOSAL_TEXT_LIMIT:
            yield Finding(
                PROPOSALS,
                f"{name}'s text is {len(proposal.text)} characters, more than the "
                f"{PROPOSAL_TEXT_LIMIT} a proposal may have: ask one short question",
                number,
            )
        if url := _URL.search(proposal.text):
            yield Finding(
                PROPOSALS,
                f"{name}'s text holds a URL ({url[0]}): a proposal is plain text, linking only "
                "pages, as [[slug]]",
                number,
            )
        for slug in dict.fromkeys(proposal.links if proposal.is_open else ()):
            if slug not in resolvable:
                yield Finding(
                    PROPOSALS,
                    f"{name} links [[{slug}]], which has no page: an open proposal links only "
                    "the pages it is about",
                    number,
                )
        # A citation that isn't in L0 at all is the citations rule's to report.
        last = proposal.citations[-1]
        if not proposal.is_open and last in l0.episode_ids and last not in owners_word:
            yield Finding(
                PROPOSALS,
                f"{name} is answered, but its last citation, {last}, isn't one of the owner's "
                "chat exchanges: only the owner's answer in chat resolves a proposal, cited "
                "last",
                number,
            )
    if tuple(name for _, name in headings) != expected:
        yield Finding(
            PROPOSALS,
            f"wrong sections: expected {', '.join(f'`## {name}`' for name in expected)}, in "
            f"that order; got {', '.join(f'`## {name}`' for _, name in headings) or 'none'}",
            headings[0][0] if headings else None,
        )


def _retired(files: Sequence[WikiFile]) -> Iterator[Finding]:
    """`retired.md` holds only retirement lines, each slug once, after an optional
    `# <title>` line. The host writes it (`retirement.py`), so this fails only a file someone
    else changed."""
    file = next((file for file in files if file.path == RETIRED), None)
    first: dict[str, int] = {}
    """The line each slug is first listed on."""
    titled = False
    for number, line in enumerate(file.lines if file else (), start=1):
        if not line.strip():
            continue
        if line.startswith("# ") and not titled and not first:
            titled = True
            continue
        retirement = parse_retirement(line)
        if retirement is None:
            yield Finding(
                RETIRED,
                f"malformed retirement: expected `{RETIREMENT_FORMAT}`, got {line!r}",
                number,
            )
        elif retirement.slug in first:
            yield Finding(
                RETIRED,
                f"{retirement.slug} is already listed, on line {first[retirement.slug]}: a "
                "retired slug is listed once",
                number,
            )
        else:
            first[retirement.slug] = number


# Warnings


def _orphans(files: Sequence[WikiFile], pages: Sequence[Page]) -> Iterator[Finding]:
    """A page no other page and no day log links to. The files at the wiki's top level don't
    count: the index lists every page, and a proposal about a page doesn't place it."""
    linked_from: dict[str, set[str]] = defaultdict(set)
    for file in files:
        if "/" in file.path:
            for line in file.lines:
                for slug in links(line):
                    linked_from[slug].add(file.path)
    for page in pages:
        if not linked_from[page.slug] - {page.path}:
            yield Finding(
                page.path,
                f"an orphan page: nothing in the wiki links [[{page.slug}]]",
                kind=WarningKind.ORPHAN,
                pages=(page.path,),
            )


def _aging_loops(pages: Sequence[Page], thresholds: Thresholds) -> Iterator[Finding]:
    """An open loop past its due date, or open longer than the loop age."""
    for page in pages:
        section = page.section(OPEN_LOOPS)
        for number, line in section.lines if section else ():
            loop = parse_loop(line)
            if loop is None or not loop.is_open:
                continue
            age = (thresholds.today - loop.opened).days
            if loop.due is not None and loop.due < thresholds.today:
                late = (thresholds.today - loop.due).days
                yield Finding(
                    page.path,
                    f"a loop past due: due {loop.due}, {late} days ago",
                    number,
                    kind=WarningKind.LOOP_PAST_DUE,
                    pages=(page.path,),
                    episodes=loop.citations,
                )
            elif age > thresholds.loop_age_days:
                yield Finding(
                    page.path,
                    f"a loop open {age} days, more than {thresholds.loop_age_days}: is it "
                    "settled, or still owed?",
                    number,
                    kind=WarningKind.LOOP_AGING,
                    pages=(page.path,),
                    episodes=loop.citations,
                )


def _duplicate_candidates(pages: Sequence[Page]) -> Iterator[Finding]:
    """Two pages that may be one: a shared alias, an alias that's another page's slug or name,
    or near-identical slugs."""
    aliased: dict[str, str] = {}
    """The first page with each alias, by its folded form."""
    for page in pages:
        for alias in dict.fromkeys(_folded(a) for a in page.aliases):
            first = aliased.setdefault(alias, page.path)
            if first != page.path:
                yield Finding(
                    page.path,
                    f"a duplicate candidate: it shares the alias {alias} with {first}",
                    kind=WarningKind.DUPLICATE,
                    pages=(page.path, first),
                )
    by_slug = {page.slug: page for page in pages}
    by_name = {_folded(page.title): page for page in pages if page.title}
    for page in pages:
        for alias in dict.fromkeys(page.aliases):
            named = by_slug.get(slugify(alias))
            if named is not None and named is not page:
                yield Finding(
                    page.path,
                    f"a duplicate candidate: its alias {alias} is the slug of {named.path}",
                    kind=WarningKind.DUPLICATE,
                    pages=(page.path, named.path),
                )
                continue
            named = by_name.get(_folded(alias))
            if named is not None and named is not page:
                yield Finding(
                    page.path,
                    f"a duplicate candidate: its alias {alias} is the name of {named.path}",
                    kind=WarningKind.DUPLICATE,
                    pages=(page.path, named.path),
                )
    for index, page in enumerate(pages):
        for other in pages[:index]:
            if _nearly(page.slug, other.slug):
                yield Finding(
                    page.path,
                    f"a duplicate candidate: its slug is nearly {other.path}'s",
                    kind=WarningKind.DUPLICATE,
                    pages=(page.path, other.path),
                )


def _folded(name: str) -> str:
    return " ".join(name.split()).casefold()


def _nearly(first: str, second: str) -> bool:
    """Whether two slugs are the same but for hyphens, or one letter (`david-kim-2`)."""
    a, b = first.replace("-", ""), second.replace("-", "")
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long = sorted((a, b), key=len)
    index = next(
        (i for i, (x, y) in enumerate(zip(short, long, strict=False)) if x != y), len(short)
    )
    return short[index:] == long[index + 1 :]


def _slugs_without_pages(
    tree: Path,
    pages: Sequence[Page],
    thresholds: Thresholds,
    engaged: Collection[str],
    retired: Collection[str],
) -> Iterator[Finding]:
    """A slug the owner has engaged with, linked on enough days to need a page, that has none.

    An unengaged slug is left alone: a person the owner never engaged with gets no page at
    the threshold, and before a slug has a page nothing here tells a person's from an
    organization's. So is one of the `retired`, the slugs whose retirement stands
    (`retirement.py`): dreaming cleared its page, and the owner hasn't engaged with it since.
    """
    with_pages = {page.slug for page in pages}
    for slug, days in sorted(linked_days(tree).items()):
        if slug not in engaged or slug in retired:
            continue
        if len(days) >= thresholds.page_threshold and slug not in with_pages:
            yield Finding(
                f"{DAYS}/{days[-1].isoformat()}.md",
                f"`{slug}` is linked on {len(days)} days, reaching the page threshold of "
                f"{thresholds.page_threshold}, but has no page",
                kind=WarningKind.SLUG_WITHOUT_PAGE,
                slug=slug,
            )


def _needs_lookup(files: Sequence[WikiFile]) -> Iterator[Finding]:
    """Outside information still marked as needing a lookup, in a page or a day log."""
    for file in files:
        if "/" not in file.path:
            continue
        for number, line in enumerate(file.lines, start=1):
            for match in NEEDS_LOOKUP.finditer(line):
                yield Finding(
                    file.path,
                    f"needs lookup: {match['question']}",
                    number,
                    kind=WarningKind.NEEDS_LOOKUP,
                    pages=() if file.is_day_log else (file.path,),
                )


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return len(text) == 10


_RULES: tuple[Rule, ...] = (_observations, _pages, _cited_episodes_exist)
