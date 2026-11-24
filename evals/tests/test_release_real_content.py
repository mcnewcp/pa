"""The release check's rules for real content (`pa-eval release`): findings that hold a name
the case holds, an address or a domain outside the reserved example domains, words the case
holds, or a long number are refused, and a refusal never prints what it found.

The case here is synthetic. Its people, its studio, and its sentences stand in for real ones,
and each test's findings break one rule, mostly in a way meant to get around it.

A run of words that is also in the repository passes the check, and so does a dotted name the
repository holds. This file is in the repository. So the case's sentences are written backwards
here (`forwards` turns them round), and a domain that must be refused is put together from its
parts, or the check would pass them for the wrong reason."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pa_home.cut import Cut


def forwards(backwards: str) -> str:
    """The words of `backwards` in the order the case holds them."""
    return " ".join(reversed(backwards.split()))


def full_width(text: str) -> str:
    """`text` in the full-width forms of its letters and digits, which read the same."""
    return "".join(chr(ord(c) + 0xFEE0) if c.isalnum() else c for c in text)


SUBJECT = forwards("Saturday second the to moves firing Glaze")
BODY_LINE = forwards("wall left the along cracked kiln big The")
SECOND_BODY_LINE = forwards("noon before bowls heron the Bring")
PAGE_LINE = forwards("bucket glaze blue the behind key studio spare the Keeps")
DAY_LINE = forwards("Thursday on shelf sgraffito the about asked me")

MAIL = f"""\
Message-ID: <kiln-0001@mail.pottery.example>
Date: Tue, 22 Sep 2026 09:15:00 -0500
From: Ottoline Varga <ovarga@pottery.example>
To: Argus McNevans <argus@example.com>
Cc: "Oyelaran, Temperance" <temperance@pottery.example>,
 "Dr. Cornelius Q. Abernathy" <cq@pottery.example>, Mina <mina@pottery.example>
Subject: {SUBJECT}
Content-Type: text/plain; charset="utf-8"

{BODY_LINE}, so every piece waits for the small one.
{SECOND_BODY_LINE}.
"""
PERSON = f"""\
---
type: person
aliases: [Mina Stroud-Okafor, Mina]
emails: [mina@pottery.example]
status: active
summary: Runs the front desk.
updated: 2026-09-22
---
# Billie Stroud

## Current state
{PAGE_LINE}. [ep:gmail_0000000000000000]

## Open loops

## Notes
"""
ORGANIZATION = """\
---
type: organization
aliases: [the pottery place]
domains: [pottery.example]
status: active
summary: Where the classes are.
updated: 2026-09-22
---
# Kiln Collective

## Current state

## Open loops

## Notes
"""
TOPIC = """\
---
type: topic
aliases: [bowl night]
status: active
summary: The autumn class.
updated: 2026-09-22
---
# Heron bowls

## Current state

## Decisions

## Open loops

## Notes
"""


@pytest.fixture
def also_in_the_case(also_in_the_case):
    also_in_the_case.payloads["gmail/kiln.eml"] = MAIL
    also_in_the_case.wiki["people/wilhelmina-stroud.md"] = PERSON
    also_in_the_case.wiki["orgs/quince-studio.md"] = ORGANIZATION
    also_in_the_case.wiki["topics/heron-bowls.md"] = TOPIC
    # The second line links a topic, and someone who has no page yet.
    also_in_the_case.wiki["days/2026-09-22.md"] = (
        f"- {DAY_LINE} with [[wilhelmina-stroud]] [ep:gmail_0000000000000000]\n"
        "- [[fennimore-quill|Fenn Quillfeather]] signed up for [[heron-bowls]] "
        "[ep:gmail_0000000000000000]\n"
    )
    also_in_the_case.wiki["retired.md"] = "- cuthbert-plumtree · retired 2026-09-20\n"
    return also_in_the_case


def first(findings: dict[str, Any], **fields: Any) -> None:
    findings["findings"][0].update(fields)


def released(desk) -> dict[str, Any]:
    """The released findings, after a release that went through."""
    return json.loads((desk.released / "findings.json").read_text())


# Names.

HELD_NAMES = {
    "a participant's display name": "Ottoline Varga",
    "a display name written family name first": "Temperance Oyelaran",
    "a display name with a title and an initial": "Cornelius Abernathy",
    "a person's slug": "Wilhelmina Stroud",
    "a person's page name": "Billie Stroud",
    "a person's alias": "Mina Stroud-Okafor",
    "an organization's slug": "Quince Studio",
    "an organization's page name": "Kiln Collective",
    "an organization's alias": "the pottery place",
    "a slug that is linked and has no page": "Fennimore Quill",
    "what a link to that slug shows": "Fenn Quillfeather",
    "a slug whose page dreaming retired": "Cuthbert Plumtree",
    "the owner's name": "Argus McNevans",
}


@pytest.mark.parametrize("name", HELD_NAMES.values(), ids=HELD_NAMES.keys())
@pytest.mark.parametrize("case", [str.upper, str.lower, str.swapcase], ids=lambda c: c.__name__)
def test_a_name_of_two_words_that_the_case_holds_is_refused_in_any_letter_case(
    desk, leavings, well_formed, name: str, case
):
    first(well_formed, mechanism=f"The merge drops {case(name)} from the older page.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=1)

    assert "- findings.json: f1.mechanism: the name " in notes


AROUND_THE_NAMES_RULE = {
    "a line break between the words": "Ottoline\nVarga",
    "a hyphen between the words": "ottoline-varga",
    "an underscore between the words": "ottoline_varga",
    "nothing between the words": "OttolineVarga",
    "punctuation around the words": "(Ottoline, Varga's)",
    "a zero-width space inside a word": "Otto\u200bline Varga",
    "a soft hyphen inside a word": "Ottoline Var\u00adga",
    "a control character inside a word": "Otto\x01line Varga",
    "full-width letters": full_width("Ottoline Varga"),
    "an accent the case doesn't write": "Ott\u00f3line V\u00e4rga",
    "a title the case writes, too": "Dr. Cornelius Q. Abernathy",
    "the family name first, as the case writes it": "Oyelaran, Temperance",
    "a slug as it is": "wilhelmina-stroud",
    "the end of a longer alias": "pottery place",
}


@pytest.mark.parametrize("text", AROUND_THE_NAMES_RULE.values(), ids=AROUND_THE_NAMES_RULE.keys())
def test_a_name_is_refused_however_it_is_written(desk, leavings, well_formed, text: str):
    well_formed["summary"] = f"The dreamed wiki files {text} under the wrong page."
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=1)

    assert "- findings.json: summary: the name " in notes


def test_a_name_split_between_two_fields_is_refused(desk, leavings, well_formed):
    first(well_formed, mechanism="The merge loses Ottoline", suggestion="Varga should stay.")
    desk.diagnose(leavings(well_formed))

    desk.refused(desk.release(), names=1)


def test_a_name_in_a_label_for_a_set_of_runs_is_refused(desk, leavings, well_formed):
    first(well_formed, runs={"ottoline-varga": [1, 3]})
    desk.diagnose(leavings(well_formed))

    # It is no label of the case's, either.
    notes = desk.refused(desk.release(), names=1, labels=1)

    assert "- findings.json: f1.runs: the name 'ottoline varga'" in notes


def test_every_name_in_the_findings_is_counted_and_noted(desk, leavings, well_formed):
    well_formed["summary"] = "Ottoline Varga and Quince Studio share a page."
    first(well_formed, suggestion="Split Ottoline Varga out again.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), names=3)

    # The notes say what was found, where, and what in the case holds it.
    assert (
        "- findings.json: summary: the name 'ottoline varga', which the case holds (a "
        "participant's display name)\n"
    ) in notes
    assert (
        "- findings.json: summary: the name 'quince studio', which the case holds (the slug of "
        "wiki/orgs/quince-studio.md)\n"
    ) in notes
    assert "- findings.json: f1.suggestion: the name 'ottoline varga'" in notes


NOT_A_HELD_NAME = {
    "a topic's slug": "heron bowls",
    "a topic's alias": "bowl night",
    "a one-word display name": "Mina",
    "a one-word alias": "mina said so",
    "one of the owner's names, of one word": "Gus",
    "one word of a name": "Ottoline",
    "the other word of a name": "Varga",
    "a name's words apart": "Ottoline asked Varga",
    "a name's words in the other order": "Varga Ottoline",
    "a name's words inside longer ones": "Ottolines Vargas",
    "the linking words an alias starts with": "the pottery",
}


@pytest.mark.parametrize("text", NOT_A_HELD_NAME.values(), ids=NOT_A_HELD_NAME.keys())
def test_a_topic_and_a_name_of_one_word_dont_refuse_findings(
    desk, leavings, well_formed, text: str
):
    well_formed["summary"] = f"The dreamed wiki files {text} under the wrong page."
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    assert released(desk)["summary"] == well_formed["summary"]


def test_a_person_page_that_cant_be_read_as_one_still_gives_its_names(
    desk, cut: Cut, leavings, well_formed
):
    # A cut is frozen, so this is the test's own tampering: the page loses its frontmatter's
    # closing line, as a wiki that was never linted might hold it.
    page = cut.path / "wiki" / "people" / "wilhelmina-stroud.md"
    page.write_text(PERSON.replace("---\n# Billie", "# Billie"))
    well_formed["summary"] = "Billie Stroud, Wilhelmina Stroud, and Mina Stroud-Okafor are one."
    desk.diagnose(leavings(well_formed))

    desk.refused(desk.release(), names=3)


# Episodes.


@pytest.mark.parametrize("episode", ["gmail_1234567890123456", "obsidian_00000000000000aa"])
def test_an_episode_that_isnt_in_the_case_is_refused(
    desk, cut: Cut, leavings, well_formed, episode: str
):
    # An episode's id is 16 hex characters, which can spell a number. So an id is held to the
    # case's own, and the rule for digits never reads this field.
    assert episode not in cut.episode_ids()
    first(well_formed, episodes=["gmail_94f6b4cbc55a3b85", episode])
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), episodes=1)

    assert f"- findings.json: f1.episodes: {episode!r} isn't an episode in the case" in notes


def test_the_cases_own_episodes_pass_whatever_digits_their_ids_hold(
    desk, cut: Cut, leavings, well_formed
):
    episodes = sorted(cut.episode_ids())
    # One of them has seven digits in a row.
    assert "obsidian_0510599eb323eee0" in episodes
    first(well_formed, episodes=episodes)
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    assert released(desk)["findings"][0]["episodes"] == episodes


# Addresses and domains. A domain that must be refused is put together here from its parts,
# since a dotted name that is in the repository passes, and this file is in it.

STUDIO = "sgraffito-works" + "." + "com"


def address(local: str, domain: str) -> str:
    return f"{local}@{domain}"


OUTSIDE_THE_RESERVED_DOMAINS = {
    "an address": address("ottoline", STUDIO),
    "an address in capitals": address("OTTOLINE", STUDIO.upper()),
    "an address in angle brackets": f"<{address('o.varga', STUDIO)}>",
    "an address with a zero-width space in it": address("ovarga", "sgraffito-\u200bworks" + ".com"),
    "an address at a host of one label": address("ovarga", "quincehost"),
    "an address at a lookalike of a reserved domain": address("ovarga", "examp1e" + ".com"),
    "an address under a domain that only starts as a reserved one does": address(
        "ovarga", "example.com" + ".quince" + ".net"
    ),
    "an address at a domain that only ends as a reserved one does": address(
        "ovarga", "quinceexample" + ".org"
    ),
    "a domain": STUDIO,
    "a domain in capitals": STUDIO.upper(),
    "a domain with a host before it": "mail." + STUDIO,
    "a domain in a link": f"https://{STUDIO}/classes?term=autumn",
    "a domain at the end of a sentence": f"The page lists {STUDIO}.",
    "a domain with an ideographic full stop": "sgraffito-works" + "\u3002" + "com",
    "a domain in full-width letters": full_width("sgraffito") + "-works" + "." + "com",
    "a lookalike of a reserved domain": "example" + "." + "community",
    "a domain that only ends as a reserved one does": "quinceexample" + "." + "com",
    "a file name that isn't in the repository": "quince-ledger" + "." + "md",
}


@pytest.mark.parametrize(
    "text", OUTSIDE_THE_RESERVED_DOMAINS.values(), ids=OUTSIDE_THE_RESERVED_DOMAINS.keys()
)
def test_an_address_or_a_domain_outside_the_reserved_example_domains_is_refused(
    desk, leavings, well_formed, text: str
):
    first(well_formed, suggestion=f"Have lint refuse {text} on a page.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), addresses=1)

    assert "- findings.json: f1.suggestion: the " in notes
    assert "is outside the reserved example domains" in notes


def test_an_address_is_refused_though_the_repository_holds_its_domain(desk, leavings, well_formed):
    # The repository's fixtures hold this domain, so by itself it passes. An address never does.
    well_formed["summary"] = "Mail from gmail.com is filed twice, as ovarga" + "@gmail.com shows."
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), addresses=1)

    assert "- findings.json: summary: the address 'ovarga@gmail.com' is outside" in notes


# A link is never excused: the repository names hosts that anyone has pages at. This file is
# in the repository, so each of these hosts is a dotted name the repository holds.

LINKS = {
    "a link to a host the repository names": "https://github.com/a-private-person/tax-records",
    "a link without its scheme": "claude.ai/chat/3f2a9c1e",
    "a link with a question": "https://www.icloud.com?shared=lease",
    "a link to a file of the repository's name": "http://dreaming.py",
}


@pytest.mark.parametrize("text", LINKS.values(), ids=LINKS.keys())
def test_a_link_is_refused_in_the_findings_though_the_repository_holds_its_host(
    desk, leavings, well_formed, text: str
):
    first(well_formed, suggestion=f"Compare it with {text} first.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), addresses=1)

    assert "- findings.json: f1.suggestion: the link to " in notes
    assert "is outside the reserved example domains" in notes


INSIDE_THE_RESERVED_DOMAINS_OR_NO_DOMAIN = {
    "an address at a reserved domain": "argus@example.com",
    "an address under a reserved domain": "desk@mail.example.org",
    "an address at a reserved top-level domain": "desk@pottery.example",
    "the other reserved names": "a@example.net, b@host.test, and c@pa.invalid",
    "a reserved domain": "www.example.com and POTTERY.EXAMPLE",
    "a file in the repository": "librarian/CLAUDE.md and dreaming.py",
    "a file in the repository, by its line": "core/src/pa_core/librarian/dreaming.py:120",
    "a directory of the repository": "the copy's wiki.git/ and deploy/home/",
    "a link to a reserved domain": "https://pottery.example/classes?term=autumn",
    "a host the repository names, by itself": "gmail.com",
    "a module in the repository": "pa_core.librarian.lint",
    "code the repository holds": "cut.episode_ids() and Path.is_file()",
    "a dotted name the referral holds": "kiln_report.final",
    "an abbreviation": "e.g. the a.m. run, i.e. the first",
    "a version and a figure": "v0.14.1 scored 2.5 of 3.0",
    "a sentence's end": "It merged. Then it stopped.",
    "a decorator": "@dataclass leaves the field out",
}


@pytest.mark.parametrize(
    "text",
    INSIDE_THE_RESERVED_DOMAINS_OR_NO_DOMAIN.values(),
    ids=INSIDE_THE_RESERVED_DOMAINS_OR_NO_DOMAIN.keys(),
)
def test_the_reserved_example_domains_and_the_repositorys_dotted_names_pass(
    desk, referral, leavings, well_formed, text: str
):
    referral.write_text(referral.read_text() + "\nThe harness wrote `kiln_report.final`.\n")
    first(well_formed, mechanism=f"The host reads {text} and files it twice.")
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    assert released(desk)["findings"][0]["mechanism"] == well_formed["findings"][0]["mechanism"]


# Quoted text. The case's sentences are written backwards at the top of this file, so the
# repository doesn't hold them.

SIX_WORDS = " ".join(BODY_LINE.split()[:6])

REPEATS_THE_CASE = {
    "a body's words": BODY_LINE,
    "a subject's words": SUBJECT,
    "a person page's line": PAGE_LINE,
    "a day log's line": DAY_LINE,
    "six words": SIX_WORDS,
    "six words in capitals": SIX_WORDS.upper(),
    "six words with other things between them": SIX_WORDS.replace(" ", ",\n- "),
    "six words in a longer sentence": f"Then, {SIX_WORDS}, as it says there",
    "six words in quotation marks": f"\u201c{SIX_WORDS}\u201d",
    "six words with accents and a zero-width space": SIX_WORDS.replace("i", "\u00ef\u200b"),
    "six words over the end of a body's line": f"small one. {SECOND_BODY_LINE}",
}


@pytest.mark.parametrize("text", REPEATS_THE_CASE.values(), ids=REPEATS_THE_CASE.keys())
def test_six_words_in_a_row_from_the_case_are_refused(desk, leavings, well_formed, text: str):
    first(well_formed, mechanism=f"The page says: {text}")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), quotes=1)

    assert "- findings.json: f1.mechanism: " in notes
    assert " words in a row that the case holds (" in notes


def test_a_quote_split_between_two_fields_is_refused(desk, leavings, well_formed):
    words = SIX_WORDS.split()
    first(
        well_formed,
        mechanism=f"The page starts: {' '.join(words[:3])}",
        suggestion=f"{' '.join(words[3:])} is how it goes on.",
    )
    desk.diagnose(leavings(well_formed))

    desk.refused(desk.release(), quotes=1)


def test_each_quote_is_counted_once_and_noted_with_where_the_case_holds_it(
    desk, cut: Cut, leavings, well_formed
):
    well_formed["summary"] = f"One page says: {PAGE_LINE}. The mail says: {BODY_LINE}!"
    first(well_formed, suggestion=f"Keep this out of the summary: {SUBJECT}")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), quotes=3)

    assert (
        "- findings.json: summary: 10 words in a row that the case holds "
        f"(wiki/people/wilhelmina-stroud.md): {PAGE_LINE.lower()!r}\n"
    ) in notes
    mail = next(
        path.stem
        for path in cut.l0.glob("episodes/gmail/*/*.json")
        if json.loads(path.read_text())["subject"] == SUBJECT
    )
    assert (
        f"- findings.json: summary: 8 words in a row that the case holds ({mail}'s body): "
        f"{BODY_LINE.lower()!r}\n"
    ) in notes
    assert (
        f"- findings.json: f1.suggestion: 7 words in a row that the case holds ({mail}'s "
        f"subject): {SUBJECT.lower()!r}\n"
    ) in notes


DOESNT_REPEAT_THE_CASE = {
    "five words": " ".join(BODY_LINE.split()[:5]),
    "six words that aren't in a row": SIX_WORDS.replace(" cracked", ", as it says, cracked"),
    "six words in another order": " ".join(reversed(SIX_WORDS.split())),
    # The fixture corpus is in the case, and in the repository.
    "words the repository holds too": (
        "Mara thinks weekday evenings work best, since her late shifts are mostly weekends."
    ),
    # A topic's page in the case holds these six, as every topic's page in the repository does.
    "a page's sections": "Current state, Decisions, Open loops, Notes",
}


@pytest.mark.parametrize("text", DOESNT_REPEAT_THE_CASE.values(), ids=DOESNT_REPEAT_THE_CASE.keys())
def test_fewer_words_and_words_the_repository_holds_pass(desk, leavings, well_formed, text: str):
    first(well_formed, mechanism=f"The page says: {text}")
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    assert released(desk)["findings"][0]["mechanism"] == well_formed["findings"][0]["mechanism"]


def test_words_the_referral_holds_pass(desk, referral, leavings, well_formed):
    referral.write_text(f"{referral.read_text()}\nA run answered: {BODY_LINE}.\n")
    first(well_formed, mechanism=f"The answer the referral gives, {BODY_LINE}, is the page's.")
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0


def test_only_the_words_the_referral_doesnt_hold_are_refused(desk, referral, leavings, well_formed):
    referral.write_text(f"{referral.read_text()}\nA run answered: {SIX_WORDS}.\n")
    first(well_formed, mechanism=f"The page says: {BODY_LINE}")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), quotes=1)

    # The first six words are the referral's; the run that takes in the seventh isn't.
    assert "7 words in a row that the case holds" in notes


def test_a_referral_changed_where_the_session_ran_excuses_nothing_and_nothing_is_checked(
    desk, leavings, well_formed
):
    # The referral excuses what it holds, so the check takes it from the devbox. The copy kept
    # with the diagnosis came back from where the session ran, which could have written it.
    first(well_formed, mechanism=f"The page says: {BODY_LINE}. It is at {STUDIO}.")
    private = desk.diagnose(leavings(well_formed))
    with (private / "referral.md").open("a") as came_back:
        came_back.write(f"\nA run answered: {BODY_LINE}. It is at {STUDIO}.\n")

    ran = desk.release()

    # A failure, never a refusal: nothing was checked, so there are no notes.
    assert ran.code == 1
    assert ran.out == ""
    assert "isn't the referral that was given" in ran.err
    assert not desk.released.exists()
    assert not (private / "check-notes.md").exists()


def test_the_release_check_needs_the_referral_that_was_asked(desk, cut: Cut, leavings, pa_eval):
    desk.diagnose(leavings())

    with pytest.raises(SystemExit):
        pa_eval("release", "--name", "dreamed-drop", "--cut", cut.path)

    assert not desk.released.exists()


# Digits.

LONG_NUMBERS = {
    "seven digits in a row": "5550142",
    "seven digits among letters": "ref A5550142B",
    "a number with hyphens": "555-014-2368",
    "a number with brackets": "(555) 014-2368",
    "a number with dots": "555.014.2368",
    "a number with spaces": "+1 555 014 2368",
    "a number in groups of four": "4111 1111 1111 1111",
    "full-width digits": full_width("5550142"),
    "digits with a zero-width space among them": "555\u200b0142",
    "an id of an episode's shape that isn't the case's": "gmail_1234567890123456",
    "a date with more digits after it": "2026-09-22-5550142",
    "digits in a date's shape that are no date": "2026-13-45",
    "a time written as one number": "20261026T120000Z",
}


@pytest.mark.parametrize("text", LONG_NUMBERS.values(), ids=LONG_NUMBERS.keys())
def test_seven_digits_in_a_row_are_refused(desk, leavings, well_formed, text: str):
    first(well_formed, suggestion=f"Have the host keep {text} off the page.")
    desk.diagnose(leavings(well_formed))

    notes = desk.refused(desk.release(), digits=1)

    assert "- findings.json: f1.suggestion: a number of " in notes


def test_digits_in_a_label_for_a_set_of_runs_are_refused(desk, leavings, well_formed):
    first(well_formed, runs={"run-5550142": [1, 3]})
    desk.diagnose(leavings(well_formed))

    # It is no label of the case's, either.
    notes = desk.refused(desk.release(), digits=1, labels=1)

    assert "- findings.json: f1.runs: a number of 7 digits: '5550142'" in notes


def test_a_long_number_in_place_of_a_count_of_runs_is_refused(desk, leavings, well_formed):
    first(well_formed, runs={"own": [3, 5550142]})
    desk.diagnose(leavings(well_formed))

    # The label holds three runs, too.
    notes = desk.refused(desk.release(), digits=1, labels=1)

    assert "- findings.json: f1.runs: a number of 7 digits: '5550142'" in notes


SHORT_NUMBERS = {
    "six digits": "555014",
    "a date": "2026-09-22",
    "a date and a time": "2026-09-22T09:15:00-05:00, and 2026-09-22 09:15",
    "an episode of the case, by its id": "obsidian_0510599eb323eee0",
    "lines of a file": "lines 448-463",
    "counts": "2,700 files, 1,200 payloads, 64000 bytes, and 3 of 6 runs",
    "a version, an issue, and a decision": "v0.14.1, #152, and ADR-0010",
    "figures a sentence apart": "It ran in 2026. 1200 episodes were read.",
}


@pytest.mark.parametrize("text", SHORT_NUMBERS.values(), ids=SHORT_NUMBERS.keys())
def test_short_numbers_dates_and_the_cases_episode_ids_pass(desk, leavings, well_formed, text: str):
    first(
        well_formed,
        suggestion=f"Have the host note {text}.",
    )
    desk.diagnose(leavings(well_formed))

    assert desk.release().code == 0

    assert released(desk)["findings"][0]["suggestion"] == well_formed["findings"][0]["suggestion"]


# A refusal, whatever it found.


def test_every_rule_that_fails_is_counted_and_nothing_found_is_printed(
    desk, data_root, leavings, well_formed
):
    planted = ["Ottoline Varga", address("ovarga", STUDIO), STUDIO, SIX_WORDS, "555-014-2368"]
    well_formed["summary"] = f"{planted[0]} wrote from {planted[1]}, and {planted[2]} bounced."
    first(
        well_formed,
        mechanism=f"The page says: {planted[3]}",
        suggestion=f"Call {planted[4]} or {planted[4]}.",
        episodes=["gmail_1234567890123456"],
    )
    left = leavings(well_formed)
    desk.diagnose(left)

    ran = desk.release()

    notes = desk.refused(ran, episodes=1, names=1, addresses=2, quotes=1, digits=2)
    # What was found is in the notes, under the data root, and nowhere in what was printed.
    assert (desk.private / "check-notes.md").is_relative_to(data_root.path)
    for found in planted:
        assert found.lower() in notes.lower()
        assert found.lower() not in (ran.out + ran.err).lower()
    # A refused diagnosis still tells the owner something.
    assert (desk.private / "output" / "analysis.md").read_text() == (
        left / "analysis.md"
    ).read_text()


def test_findings_that_hold_nothing_real_are_released_as_they_are(desk, leavings, well_formed):
    desk.diagnose(leavings(well_formed))

    ran = desk.release()

    assert ran.code == 0
    assert released(desk) == well_formed
    for rule in ("episodes", "names", "addresses", "quotes", "digits"):
        assert f"check {rule}: 0\n" in ran.out


# The check needs what it checks against.


@pytest.mark.parametrize("unset", ["PA_OWNER_NAME", "PA_OWNER_EMAILS"])
def test_a_release_without_the_owners_identity_is_refused(
    desk, leavings, monkeypatch: pytest.MonkeyPatch, unset: str
):
    desk.diagnose(leavings())
    monkeypatch.delenv(unset)

    ran = desk.release()

    assert ran.code == 1
    assert f"Owner configuration is missing: set {unset}" in ran.err
    assert not desk.released.exists()


def test_a_case_with_an_envelope_that_cant_be_read_fails_the_release_without_quoting_it(
    desk, cut: Cut, leavings
):
    desk.diagnose(leavings())
    # The test's own tampering with a cut, which is frozen: an envelope is put in place of one
    # the cut shares with L0.
    envelope = next(cut.l0.glob("episodes/gmail/*/*.json"))
    envelope.unlink()
    envelope.write_text(json.dumps({"subject": "the gooseberry invoice"}))

    ran = desk.release()

    assert ran.code == 1
    assert "can't be read, so what the case holds can't be gathered" in ran.err
    assert not desk.released.exists()
