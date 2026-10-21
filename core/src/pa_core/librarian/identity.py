"""The identity table: who's who by address, built on the host from L0's metadata at every run.

It's the deterministic part of v0.12's people resolution (kept at the `archive/v0.12-claims-graph`
tag), without the resemblance scoring and the model call, so the identifiers the wiki holds come
from source metadata (spec #56, principle 3):

- **People.** Addresses are grouped by display name: two addresses are one person's when their
  display names match exactly, ignoring only case and spacing. An address seen under several
  names goes by its commonest. An address with no display name is a group of its own. Each group
  gets a suggested slug.
- **Organizations** come from mail domains, never webmail: a domain makes one organization,
  its subdomains included (`mail.birchwood.example` is Birchwood's), and the people writing or
  written to there are its members. A group named for the domain and only at it ("ClearFlow
  Gutters <hello@clearflowgutters.example>") is the organization's own address, not a person:
  the organization takes its name and its slug.
- **The owner** has only the configured addresses. An address the owner isn't configured with is
  never theirs, even under the owner's name; it's a group of its own, marked as using the name.
- **Forged mail** is email sent from one of the owner's addresses without the Sent label: no one
  can write as the owner from outside the owner's mailbox.

Slugs are suggested from names (an organization with none goes by its domain, an address with
no name by its local part). They share one namespace, with `me` kept for the owner, and are
handed out in the order their people and organizations were first seen, so they stay put as L0
grows.

The table reads every episode the librarian could be offered, so mail that episode selection
always skips (Promotions, Social) is left out: bulk senders would flood it and never be observed.
Nicknames that appear only in bodies ("Okie") are left to the librarian, which reads the episodes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from pa_core.envelope import Envelope, Kind, Role
from pa_core.librarian.wiki import ORGANIZATION, OWNER, PERSON, slugify
from pa_core.owner import Owner
from pa_core.selection import SKIPPED, category

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

_SENT_LABEL = "sent"
_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})
"""Labels under a country's domain (`co.uk`): the label before them names the organization."""


@dataclass(frozen=True)
class OwnerIdentity:
    """The owner, as configured: their names and their only addresses."""

    name: str
    other_names: tuple[str, ...]
    addresses: tuple[str, ...]
    organizations: tuple[str, ...]
    """The slugs of the organizations at the owner's addresses."""
    slug: str = OWNER


@dataclass(frozen=True)
class PersonIdentity:
    """One group of addresses sharing a display name, with the slug suggested for it."""

    slug: str
    name: str | None
    """The display name, as most often spelled; None for an address seen with no name."""
    addresses: tuple[str, ...]
    organizations: tuple[str, ...]
    """The slugs of the organizations at the group's addresses."""
    aliases: tuple[str, ...] = ()
    """Other display names its addresses were seen under."""
    uses_owner_name: bool = False
    """Its name is one of the owner's, but none of its addresses is: it is not the owner."""


@dataclass(frozen=True)
class OrganizationIdentity:
    """An organization by its mail domain, with the slug suggested for it."""

    slug: str
    name: str | None
    """Its name, from its own addresses; None when it has none."""
    domains: tuple[str, ...]
    """Its domain and subdomains, as seen in addresses."""
    addresses: tuple[str, ...]
    """Its own addresses: those whose display name spells its domain."""
    members: tuple[str, ...]
    """The slugs of the people with an address there (`me` for the owner)."""


@dataclass(frozen=True)
class ForgedMail:
    """Email from one of the owner's addresses that the owner's mailbox didn't send."""

    episode_id: str
    address: str


@dataclass(frozen=True)
class IdentityTable:
    """Who's who in L0's metadata: the owner, people, organizations, and forged mail."""

    owner: OwnerIdentity
    people: tuple[PersonIdentity, ...]
    organizations: tuple[OrganizationIdentity, ...]
    forged: tuple[ForgedMail, ...]
    addresses: frozenset[str]
    """Every address the table read, the owner's included when seen."""

    @property
    def domains(self) -> frozenset[str]:
        """The mail domain of every address the table read, webmail included."""
        return frozenset(d for d in (_domain(a) for a in self.addresses) if d is not None)

    def to_json(self) -> dict[str, object]:
        """The table as JSON for a session: optional fields only when they say something."""
        return {
            "owner": {
                "slug": self.owner.slug,
                "name": self.owner.name,
                "other_names": list(self.owner.other_names),
                "addresses": list(self.owner.addresses),
                "organizations": list(self.owner.organizations),
            },
            "people": [_person_json(person) for person in self.people],
            "organizations": [
                {
                    "slug": org.slug,
                    "name": org.name,
                    "domains": list(org.domains),
                    "addresses": list(org.addresses),
                    "members": list(org.members),
                }
                for org in self.organizations
            ],
            "forged": [
                {"episode": forged.episode_id, "address": forged.address} for forged in self.forged
            ],
        }


def _person_json(person: PersonIdentity) -> dict[str, object]:
    listed: dict[str, object] = {
        "slug": person.slug,
        "name": person.name,
        "addresses": list(person.addresses),
        "organizations": list(person.organizations),
    }
    if person.aliases:
        listed["aliases"] = list(person.aliases)
    if person.uses_owner_name:
        listed["uses_owner_name"] = True
    return listed


def identity_table(envelopes: Iterable[Envelope], owner: Owner) -> IdentityTable:
    """The identity table over `envelopes` (all of L0) for `owner`."""
    read = sorted(
        (e for e in envelopes if category(e) not in SKIPPED),
        key=lambda e: (e.occurred_at.start, e.episode_id),
    )
    mine = set(owner.email_addresses)
    names: dict[str, Counter[str]] = {}
    """Each address's display names, cleaned; addresses and names in the order first seen."""
    forged: list[ForgedMail] = []
    for envelope in read:
        for participant in envelope.participants:
            address = participant.identifier.strip().lower()
            seen = names.setdefault(address, Counter())
            name = _clean(participant.name)
            if name and "@" not in name:
                seen[name] += 1
            if participant.role is Role.SENDER and address in mine and _forged(envelope):
                forged.append(ForgedMail(envelope.episode_id, address))

    # Everyone's addresses in the order first seen, the owner's first.
    position = {a: i for i, a in enumerate(dict.fromkeys([*owner.email_addresses, *names]))}

    domains: dict[str, list[str]] = {}
    """Each organization's domains, by its registered domain, in the order first seen."""
    for address in position:
        domain = _org_domain(address)
        if domain is not None:
            found = domains.setdefault(_registered(domain), [])
            if domain not in found:
                found.append(domain)
    org_first = {r: position[a] for a in reversed(position) if (r := _registered_of(a))}

    groups: dict[str, list[str]] = {}
    """Each group's addresses, by its display name's key (its address, with no name)."""
    for address, seen in names.items():
        if address not in mine:
            groups.setdefault(_key(_commonest(seen)) or address, []).append(address)
    spellings: dict[str, Counter[str]] = {}
    for key, addresses in groups.items():
        spellings[key] = Counter()
        for address in addresses:
            spellings[key].update(names[address])

    # A group named for its organization's domain, all at that domain, is the organization's
    # own address: "ClearFlow Gutters <hello@clearflowgutters.example>".
    own: dict[str, str] = {}
    """The organization's own group, by registered domain."""
    for key, addresses in groups.items():
        registered = {_registered(d) if d else None for d in map(_org_domain, addresses)}
        domain = registered.pop() if len(registered) == 1 else None
        if (
            domain is not None
            and domain not in own
            and _compact(key) == _compact(_domain_label(domain))
        ):
            own[domain] = key

    # Slugs go to people and organizations in the order first seen, so they stay put as L0 grows.
    slugs = _Slugs()
    seen = [(org_first[domain], ORGANIZATION.type, domain) for domain in domains]
    named_orgs = set(own.values())
    seen += [
        (position[addresses[0]], PERSON.type, key)
        for key, addresses in groups.items()
        if key not in named_orgs
    ]
    org_slugs: dict[str, str] = {}
    person_slugs: dict[str, str] = {}
    for _, kind, holder in sorted(seen):
        if kind == ORGANIZATION.type:
            name = _commonest(spellings[own[holder]]) if holder in own else ""
            base = slugify(name) if name else _domain_label(holder)
            org_slugs[holder] = slugs.assign(base, f"{kind}:{holder}")
        else:
            name = _commonest(spellings[holder])
            base = slugify(name) if name else _address_slug(groups[holder][0])
            person_slugs[holder] = slugs.assign(base, f"{kind}:{holder}")

    owner_names = {_key(n) for n in (owner.name, *owner.other_names)}
    members: dict[str, set[str]] = {domain: set() for domain in domains}
    for address in owner.email_addresses:
        _join(members, address, OWNER)
    people: list[PersonIdentity] = []
    for key, slug in person_slugs.items():
        addresses = groups[key]
        name = _commonest(spellings[key]) or None
        people.append(
            PersonIdentity(
                slug=slug,
                name=name,
                addresses=tuple(sorted(addresses)),
                organizations=_organizations_of(addresses, org_slugs),
                aliases=tuple(sorted({n for n in spellings[key] if _key(n) != key})),
                uses_owner_name=name is not None and key in owner_names,
            )
        )
        for address in addresses:
            _join(members, address, slug)
    organizations = [
        OrganizationIdentity(
            slug=org_slugs[domain],
            name=_commonest(spellings[own[domain]]) if domain in own else None,
            domains=tuple(sorted(found)),
            addresses=tuple(sorted(groups[own[domain]])) if domain in own else (),
            members=tuple(sorted(members[domain])),
        )
        for domain, found in domains.items()
    ]

    return IdentityTable(
        owner=OwnerIdentity(
            name=owner.name,
            other_names=owner.other_names,
            addresses=owner.email_addresses,
            organizations=_organizations_of(owner.email_addresses, org_slugs),
        ),
        people=tuple(sorted(people, key=lambda p: p.slug)),
        organizations=tuple(sorted(organizations, key=lambda o: o.slug)),
        forged=tuple(forged),
        addresses=frozenset(names),
    )


class _Slugs:
    """Hands out slugs in one namespace: the first to ask for one gets it, later ones a
    number."""

    def __init__(self) -> None:
        self._holders: dict[str, str] = {OWNER: "\0owner"}

    def assign(self, base: str, holder: str) -> str:
        """The slug for `holder` (the same one each time it asks), from `base`."""
        base = base or "unnamed"
        slug, number = base, 1
        while self._holders.setdefault(slug, holder) != holder:
            number += 1
            slug = f"{base}-{number}"
        return slug


def _compact(text: str) -> str:
    """A name's letters and digits alone: "ClearFlow Gutters" is `clearflowgutters`."""
    return slugify(text).replace("-", "")


def _address_slug(address: str) -> str:
    """A slug for an address with no name: its local part, then its organization's name."""
    local = slugify(address.partition("@")[0])
    registered = _registered_of(address)
    org = _domain_label(registered) if registered is not None else ""
    return "-".join(part for part in (local, org) if part)


def _join(members: dict[str, set[str]], address: str, slug: str) -> None:
    registered = _registered_of(address)
    if registered is not None:
        members[registered].add(slug)


def _organizations_of(addresses: Iterable[str], org_slugs: dict[str, str]) -> tuple[str, ...]:
    registered = {r for r in map(_registered_of, addresses) if r is not None}
    return tuple(sorted(org_slugs[r] for r in registered))


def _registered_of(address: str) -> str | None:
    """The registered domain of the organization at `address`, if there is one."""
    domain = _org_domain(address)
    return _registered(domain) if domain is not None else None


def _registered(domain: str) -> str:
    """The domain an organization registered: `birchwood.example` of `mail.birchwood.example`."""
    labels = domain.split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def _domain_label(domain: str) -> str:
    """The slug of a registered domain's name: `harrow-builders` of `harrow-builders.co.uk`."""
    return slugify(domain.split(".")[0])


def _commonest(seen: Counter[str]) -> str:
    """The commonest name, the first seen among equals; empty when there's none."""
    return seen.most_common(1)[0][0] if seen else ""


def _forged(envelope: Envelope) -> bool:
    """Whether it's email the owner's mailbox didn't send (other sources have no forging)."""
    sent = any(label.strip().casefold() == _SENT_LABEL for label in envelope.labels)
    return envelope.kind is Kind.EMAIL and not sent


def _domain(address: str) -> str | None:
    _, at, domain = address.rpartition("@")
    return domain if at and domain else None


def _org_domain(address: str) -> str | None:
    """The mail domain of `address` if an organization can come from it."""
    domain = _domain(address)
    return None if domain is None or domain in PERSONAL_MAIL_DOMAINS else domain


def _clean(name: str | None) -> str:
    return " ".join(name.split()) if name else ""


def _key(name: str) -> str:
    return _clean(name).casefold()
