"""People and organizations resolution: who each participant, speaker, and mention is.

Episodes must be offered in time order; each decision uses what earlier episodes established.

- **Participants** are resolved by address. An address seen for the first time is someone
  known by that exact name or alias, or else is identified like a mention (below); so a second
  address joins the person, and its display name becomes one of their aliases.
- **Mentions** are resolved by exact name or alias. A name matching no one exactly, or several
  people, is identified by one model call among the candidates it resembles; with none, it is
  someone new without a call. People known only from mentions are people too.
- **Organizations** come from mail domains, never personal webmail: a domain makes one
  organization, and a person writing from it is a member (`MEMBER_OF`). A person also joins
  one when an extracted statement names their organization. An organization's own address
  (a clinic's appointments desk) makes the organization itself the participant.

The owner is resolved from the instance's configuration. Resolved identities are merged into
one node; nothing is linked as the same as anything else.

Ids derive from the decision that created the node: a person first seen at an address from
that address, a person or organization first seen in a mention from that episode and name,
and an organization from its domain.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pa_core.claims.records import EdgeRecord, NodeRecord, stable_id
from pa_core.claims.schema import MEMBER_OF, ORGANIZATION, PERSON
from pa_core.librarian.identify import PartyKind, Question
from pa_core.owner import Owner

PERSONAL_MAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "att.net",
        "comcast.net",
        "fastmail.com",
        "gmail.com",
        "gmx.com",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.co.uk",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "sbcglobal.net",
        "verizon.net",
        "yahoo.co.uk",
        "yahoo.com",
        "ymail.com",
        "zoho.com",
        # The reserved example domains (RFC 2606) stand in for personal mail in the
        # synthetic corpus, where organizations use made-up `.example` domains.
        "example.com",
        "example.net",
        "example.org",
    }
)
"""Domains of personal mail: an address at one says nothing about an organization."""

MAX_CANDIDATES = 8

type Identify = Callable[[Question], int | None]
"""Asks who a name is: the index of the candidate it is, or None for someone new."""


@dataclass
class _Party:
    id: str
    kind: PartyKind
    name: str | None = None
    is_owner: bool = False
    aliases: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [self.name, *self.aliases] if self.name else list(self.aliases)

    def add_name(self, name: str | None) -> None:
        name = _clean(name)
        if not name or "@" in name:
            return
        if self.name is None:
            self.name = name
        elif _key(name) not in {_key(known) for known in self.names()}:
            self.aliases.append(name)

    def seen_in(self, episode_id: str) -> None:
        if episode_id not in self.episode_ids:
            self.episode_ids.append(episode_id)


@dataclass
class _Membership:
    id: str
    person: str
    organization: str
    episode_ids: list[str] = field(default_factory=list)


class People:
    """Everyone the episodes show, people and organizations, resolved as they are offered."""

    def __init__(self, owner: Owner, identify: Identify) -> None:
        self._identify = identify
        self._parties: list[_Party] = []
        self._by_id: dict[str, _Party] = {}
        self._by_address: dict[str, _Party] = {}
        self._by_domain: dict[str, _Party] = {}
        self._memberships: dict[str, _Membership] = {}
        me = self._add(
            _Party(stable_id("person", "owner", owner.identifier), "person", is_owner=True)
        )
        me.add_name(owner.name)
        for name in owner.other_names:
            me.add_name(name)
        for address in owner.email_addresses:
            me.addresses.append(address)
            self._by_address[address] = me
        self.owner_id = me.id

    def participant(
        self,
        address: str,
        name: str | None,
        episode_id: str,
        context: str,
        *,
        as_organization: bool = False,
        organization: str | None = None,
    ) -> str:
        """The id of whoever is at `address`, appearing in `episode_id` under `name`.

        `as_organization`: the address is an organization's own, not a person's. `organization`:
        the organization the episode says the participant writes for, by name.
        """
        address = address.strip().lower()
        domain = _org_domain(address)
        party = self._by_address.get(address)
        if party is None:
            if as_organization:
                party = self._organization_at(domain, address, organization or name, context)
            else:
                party = self._resolve(_clean(name), "person", context, episode_id, address)
            party.addresses.append(address)
            self._by_address[address] = party
        party.add_name(name)
        if party.kind == "organization":
            party.add_name(organization)
        party.seen_in(episode_id)
        if party.kind == "person" and domain is not None:
            org = self._organization_at(domain, None, organization, context)
            org.add_name(organization)
            self._join(party, org, episode_id)
        elif party.kind == "person" and _clean(organization):
            self.join(party.id, str(organization), episode_id, context)
        return party.id

    def mention(self, name: str, kind: PartyKind, episode_id: str, context: str) -> str | None:
        """The id of the person or organization an episode names as `name`; None if blank."""
        name = _clean(name)
        if not name:
            return None
        party = self._by_address.get(name.lower()) if "@" in name else None
        if party is None:
            party = self._resolve(name, kind, context, episode_id)
        party.seen_in(episode_id)
        return party.id

    def speaker(self, name: str, episode_id: str, context: str) -> str | None:
        """Who an episode says made a statement: an organization only if one has that exact
        name, otherwise a person."""
        organizations = self._exact(_clean(name), "organization")
        kind: PartyKind = "organization" if len(organizations) == 1 else "person"
        return self.mention(name, kind, episode_id, context)

    def join(self, person_id: str, organization: str, episode_id: str, context: str) -> None:
        """Record that the person `person_id` belongs to the organization named `organization`."""
        org_id = self.mention(organization, "organization", episode_id, context)
        person = self._by_id[person_id]
        if org_id is not None and person.kind == "person":
            self._join(person, self._by_id[org_id], episode_id)

    def records(self) -> tuple[list[NodeRecord], list[EdgeRecord]]:
        nodes = [_node(party) for party in self._parties]
        edges = [
            EdgeRecord(
                label=MEMBER_OF.label,
                id=membership.id,
                source=membership.person,
                target=membership.organization,
                properties={},
                episode_ids=tuple(membership.episode_ids),
            )
            for membership in self._memberships.values()
        ]
        return nodes, edges

    def _resolve(
        self,
        name: str,
        kind: PartyKind,
        context: str,
        episode_id: str,
        address: str | None = None,
    ) -> _Party:
        """Who `name` is: the one with that exact name or alias, the candidate the model
        chooses, or someone new. A new address with no name is matched by its local part."""
        exact = self._exact(name, kind)
        if len(exact) == 1:
            return exact[0]
        candidates = exact or self._resembling(name or _local_words(address or ""), kind)
        if candidates:
            question = Question(
                name=name or address or "",
                kind=kind,
                context=context,
                candidates=tuple(_describe(party) for party in candidates),
            )
            chosen = self._identify(question)
            if chosen is not None:
                party = candidates[chosen]
                party.add_name(name)
                return party
        if address is not None:
            party_id = stable_id(kind, "address", address)
        else:
            party_id = stable_id(kind, "mention", episode_id, _key(name))
        party = self._add(_Party(party_id, kind))
        party.add_name(name)
        return party

    def _organization_at(
        self, domain: str | None, address: str | None, name: str | None, context: str
    ) -> _Party:
        """The organization of a mail domain, or of its own address at a personal domain."""
        if domain is not None and domain in self._by_domain:
            return self._by_domain[domain]
        named = _clean(name)
        if named:
            exact = self._exact(named, "organization")
            org = exact[0] if len(exact) == 1 else None
        else:
            org = None
        if org is None:
            key = ("domain", domain) if domain else ("address", address or "")
            org = self._add(_Party(stable_id("organization", *key), "organization"))
            org.add_name(named)
        if domain is not None:
            org.domains.append(domain)
            self._by_domain[domain] = org
        return org

    def _join(self, person: _Party, org: _Party, episode_id: str) -> None:
        membership_id = stable_id("member_of", person.id, org.id)
        membership = self._memberships.get(membership_id)
        if membership is None:
            membership = _Membership(membership_id, person.id, org.id)
            self._memberships[membership_id] = membership
        if episode_id not in membership.episode_ids:
            membership.episode_ids.append(episode_id)
        org.seen_in(episode_id)

    def _exact(self, name: str, kind: PartyKind) -> list[_Party]:
        key = _key(name)
        if not key:
            return []
        return [
            party
            for party in self._parties
            if party.kind == kind and key in {_key(known) for known in party.names()}
        ]

    def _resembling(self, name: str, kind: PartyKind) -> list[_Party]:
        """Those of `kind` with a name or alias that `name` resembles, best first."""
        words = _words(name)
        if not words:
            return []
        scored: list[tuple[int, int, _Party]] = []
        for position, party in enumerate(self._parties):
            if party.kind != kind:
                continue
            known = [_words(known) for known in party.names()]
            known += [[_domain_word(domain)] for domain in party.domains]
            score = max((_resemblance(words, other) for other in known if other), default=0)
            if score:
                scored.append((-score, position, party))
        return [party for _, _, party in sorted(scored)[:MAX_CANDIDATES]]

    def _add(self, party: _Party) -> _Party:
        self._parties.append(party)
        self._by_id[party.id] = party
        return party


def _node(party: _Party) -> NodeRecord:
    if party.kind == "person":
        return NodeRecord(
            label=PERSON.label,
            id=party.id,
            properties={
                "name": party.name or party.addresses[0],
                "aliases": list(party.aliases),
                "addresses": list(party.addresses),
                "is_owner": party.is_owner,
            },
            episode_ids=tuple(party.episode_ids),
        )
    return NodeRecord(
        label=ORGANIZATION.label,
        id=party.id,
        properties={
            "name": party.name or (party.domains[0] if party.domains else party.id),
            "aliases": list(party.aliases),
            "domains": list(party.domains),
            "addresses": list(party.addresses),
        },
        episode_ids=tuple(party.episode_ids),
    )


def _describe(party: _Party) -> str:
    """One line for a candidate, starting with its name."""
    parts = [party.name or (party.addresses or party.domains or [party.id])[0]]
    if party.is_owner:
        parts.append("the owner")
    if party.aliases:
        parts.append(f"also known as {', '.join(party.aliases)}")
    if party.addresses:
        parts.append(f"addresses {', '.join(party.addresses)}")
    if party.domains:
        parts.append(f"mail domains {', '.join(party.domains)}")
    return " | ".join(parts)


_TITLES = frozenset({"dr", "mr", "mrs", "ms", "mx", "prof", "the", "and", "of", "at"})


def _words(name: str) -> list[str]:
    return [word for word in re.findall(r"[a-z]+", name.lower()) if word not in _TITLES]


def _resemblance(words: list[str], other: list[str]) -> int:
    """How many words the shorter name shares with the longer, if it shares them all.

    Words are alike when equal or when they start with the same three letters, so a nickname
    (Dave, David) resembles the full name; the model decides.
    """
    shorter, longer = sorted((words, other), key=len)
    if all(any(_alike(word, known) for known in longer) for word in shorter):
        return len(shorter)
    return 0


def _alike(word: str, other: str) -> bool:
    return word == other or (len(word) >= 3 and len(other) >= 3 and word[:3] == other[:3])


def _org_domain(address: str) -> str | None:
    """The mail domain of `address` if an organization can come from it."""
    _, at, domain = address.rpartition("@")
    if not at or not domain or domain in PERSONAL_MAIL_DOMAINS:
        return None
    return domain


def _domain_word(domain: str) -> str:
    return domain.split(".")[0]


def _local_words(address: str) -> str:
    """A name-like reading of an address's local part: `david.kim@…` reads as `david kim`."""
    local = address.partition("@")[0]
    return " ".join(re.findall(r"[a-z]+", local.lower()))


def _clean(name: str | None) -> str:
    return " ".join(name.split()) if name else ""


def _key(name: str) -> str:
    return _clean(name).casefold()
