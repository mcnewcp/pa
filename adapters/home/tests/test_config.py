from pa_core.owner import Owner
from pa_home.config import load_owner


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
