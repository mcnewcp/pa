from collections.abc import Callable
from pathlib import Path

import pytest

from pa_core.owner import Owner
from pa_home.config import (
    ConfigError,
    load_icloud_credentials,
    load_librarian_config,
    load_owner,
)


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


# The iCloud credential: an Apple ID, and an app-specific password in a file only the owner can
# read. Neither may appear in an error.
APPLE_ID = "zq.hush@quackmail.example"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"


def icloud_environ(tmp_path: Path, *, mode: int = 0o600, password: str = APP_PASSWORD):
    path = tmp_path / "icloud-app-password"
    path.write_text(password + "\n")
    path.chmod(mode)
    return {"PA_ICLOUD_USERNAME": APPLE_ID, "PA_ICLOUD_PASSWORD_FILE": str(path)}


def test_the_icloud_credential_is_the_apple_id_and_the_password_in_its_file(tmp_path: Path):
    credentials = load_icloud_credentials(icloud_environ(tmp_path))

    assert (credentials.username, credentials.password) == (APPLE_ID, APP_PASSWORD)
    assert APP_PASSWORD not in repr(credentials)


def test_the_password_file_defaults_to_the_owners_configuration_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config" / "pa").mkdir(parents=True)
    path = tmp_path / ".config" / "pa" / "icloud-app-password"
    path.write_text(APP_PASSWORD)
    path.chmod(0o600)

    credentials = load_icloud_credentials({"PA_ICLOUD_USERNAME": APPLE_ID})

    assert credentials.password == APP_PASSWORD


def _unset_username(environ: dict[str, str]) -> None:
    del environ["PA_ICLOUD_USERNAME"]


def _missing_file(environ: dict[str, str]) -> None:
    environ["PA_ICLOUD_PASSWORD_FILE"] = "/nonexistent/password"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_unset_username, "PA_ICLOUD_USERNAME is not set"),
        (_missing_file, "no app-specific password at /nonexistent/password"),
    ],
)
def test_a_missing_icloud_credential_is_refused(
    tmp_path: Path, change: Callable[[dict[str, str]], None], message: str
):
    environ = icloud_environ(tmp_path)
    change(environ)

    with pytest.raises(ConfigError, match=message) as refused:
        load_icloud_credentials(environ)

    assert APPLE_ID not in str(refused.value)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_password_file_others_can_read_is_refused(tmp_path: Path, mode: int):
    with pytest.raises(ConfigError, match="readable by others") as refused:
        load_icloud_credentials(icloud_environ(tmp_path, mode=mode))

    assert APP_PASSWORD not in str(refused.value)
    assert APPLE_ID not in str(refused.value)


def test_an_empty_password_file_is_refused(tmp_path: Path):
    with pytest.raises(ConfigError, match="is empty"):
        load_icloud_credentials(icloud_environ(tmp_path, password="  "))
