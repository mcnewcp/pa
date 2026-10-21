import pytest

from pa_core.owner import Owner
from pa_home.config import ConfigError, load_librarian_config, load_owner


def test_owner_identity_is_read_from_comma_separated_configuration():
    owner = load_owner(
        {
            "PA_OWNER_NAME": " Argus McNevans ",
            "PA_OWNER_EMAILS": "Argus@Example.com, argus.mcnevans@example.org,",
            "PA_OWNER_OTHER_NAMES": "Gus, Dad",
        }
    )

    assert owner == Owner(
        name="Argus McNevans",
        email_addresses=("argus@example.com", "argus.mcnevans@example.org"),
        other_names=("Gus", "Dad"),
    )
    assert owner.identifier == "argus@example.com"


def test_other_names_are_optional():
    owner = load_owner({"PA_OWNER_NAME": "Argus McNevans", "PA_OWNER_EMAILS": "argus@example.com"})

    assert owner.other_names == ()


@pytest.mark.parametrize("value", ["2.5", "0", "forty"])
def test_the_librarians_turn_cap_is_a_whole_number_of_at_least_one(value: str):
    with pytest.raises(ConfigError, match="PA_LIBRARIAN_MAX_TURNS must be a whole number"):
        load_librarian_config({"PA_LIBRARIAN_MAX_TURNS": value})

    assert load_librarian_config({"PA_LIBRARIAN_MAX_TURNS": "40"}).max_turns == 40
