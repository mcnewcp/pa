"""People resolution: who each participant and speaker is.

For now a person is resolved by address alone, and the owner by the instance's configuration.
Episodes must be offered in time order, so a person's name is the first one seen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pa_core.claims.records import NodeRecord, stable_id
from pa_core.claims.schema import PERSON
from pa_core.owner import Owner


@dataclass
class _Person:
    id: str
    name: str | None
    is_owner: bool
    aliases: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)


class People:
    def __init__(self, owner: Owner) -> None:
        self._by_address: dict[str, _Person] = {}
        self._people: list[_Person] = []
        me = self._add(stable_id("person", "owner", owner.identifier), owner.name, is_owner=True)
        me.aliases.extend(owner.other_names)
        for address in owner.email_addresses:
            me.addresses.append(address)
            self._by_address[address] = me

    def by_address(self, address: str, name: str | None, episode_id: str) -> str:
        """The id of the person at `address`, who appears in `episode_id` under `name`."""
        address = address.strip().lower()
        person = self._by_address.get(address)
        if person is None:
            person = self._add(stable_id("person", "address", address), None, is_owner=False)
            person.addresses.append(address)
            self._by_address[address] = person
        if name and name.strip():
            name = " ".join(name.split())
            if person.name is None:
                person.name = name
            elif name != person.name and name not in person.aliases:
                person.aliases.append(name)
        if episode_id not in person.episode_ids:
            person.episode_ids.append(episode_id)
        return person.id

    def records(self) -> list[NodeRecord]:
        return [
            NodeRecord(
                label=PERSON.label,
                id=person.id,
                properties={
                    "name": person.name or person.addresses[0],
                    "aliases": list(person.aliases),
                    "addresses": list(person.addresses),
                    "is_owner": person.is_owner,
                },
                episode_ids=tuple(person.episode_ids),
            )
            for person in self._people
        ]

    def _add(self, person_id: str, name: str | None, *, is_owner: bool) -> _Person:
        person = _Person(id=person_id, name=name, is_owner=is_owner)
        self._people.append(person)
        return person
