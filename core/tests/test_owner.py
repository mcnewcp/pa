import pytest

from pa_core.owner import Owner


def test_the_owners_email_addresses_are_kept_in_lower_case_like_participant_identifiers():
    owner = Owner(name="Argus McNevans", email_addresses=("Argus@Example.com", "GUS@example.org"))

    assert owner.email_addresses == ("argus@example.com", "gus@example.org")
    assert owner.identifier == "argus@example.com"


def test_an_owner_needs_an_email_address():
    with pytest.raises(ValueError, match="at least one email address"):
        Owner(name="Argus McNevans", email_addresses=())
