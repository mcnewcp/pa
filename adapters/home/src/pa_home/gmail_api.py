"""The home Gmail client: the Gmail API, read-only, with OAuth for an installed app.

The OAuth client comes from the owner's own Google Cloud project, published "In production"
and left unverified, for personal use, so its refresh token doesn't expire weekly. It still
dies after six months unused or a password change; `pa gmail auth` gets a new one. The token
lives in the owner's configuration directory, readable only by them, and never in the store or
the repository.

Every failure is a CollectionError whose reason names what failed, never what was read.
"""

from __future__ import annotations

import base64
import stat
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from pa_core.atomic import write_atomically
from pa_home.collectors import CollectionError
from pa_home.collectors.gmail import GmailMessage, HistoryExpired

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
"""Read-only: the PA can read the owner's mail and never change it."""
_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_PAGE = 500
_TIMEOUT_SECONDS = 60
# History that adds a message to what's captured: a new message, or one brought back out of
# spam, trash, or drafts (a sent draft).
_HISTORY_TYPES = ("messageAdded", "labelRemoved")
_RESCUED_FROM = frozenset({"SPAM", "TRASH", "DRAFT"})
DEFAULT_AUTH_PORT = 8765


class GoogleGmailClient:
    """The Gmail account whose token is at `token`. Nothing is read until the first call, so a
    missing or dead token fails the Gmail collection alone."""

    def __init__(self, token: Path) -> None:
        self.token = token
        self._session: AuthorizedSession | None = None

    def history_position(self) -> str:
        return str(self._get("profile")["historyId"])

    def changed_since(self, position: str) -> Sequence[str]:
        params: list[tuple[str, str | int]] = [("startHistoryId", position)]
        params += [("historyTypes", kind) for kind in _HISTORY_TYPES]
        ids: list[str] = []
        for page in self._pages("history", params, expired_on_404=True):
            for record in page.get("history", []):
                ids += [added["message"]["id"] for added in record.get("messagesAdded", [])]
                ids += [
                    removed["message"]["id"]
                    for removed in record.get("labelsRemoved", [])
                    if _RESCUED_FROM & set(removed.get("labelIds", []))
                ]
        return list(dict.fromkeys(ids))

    def ids_between(self, start: datetime, end: datetime) -> Sequence[str]:
        # Gmail's search takes seconds since the epoch; spam and trash are left out unless asked.
        query = f"after:{int(start.timestamp())} before:{int(end.timestamp())}"
        ids: list[str] = []
        for page in self._pages("messages", [("q", query)]):
            ids += [message["id"] for message in page.get("messages", [])]
        return ids

    def fetch(self, message_id: str) -> GmailMessage:
        found = self._get(f"messages/{message_id}", [("format", "raw")])
        raw = found["raw"]
        return GmailMessage(
            raw=base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
            label_ids=frozenset(found.get("labelIds", [])),
        )

    def label_ids(self, message_id: str) -> frozenset[str]:
        found = self._get(f"messages/{message_id}", [("format", "minimal")])
        return frozenset(found.get("labelIds", []))

    def label_names(self) -> Mapping[str, str]:
        labels = self._get("labels").get("labels", [])
        return {label["id"]: label["name"] for label in labels if label.get("type") == "user"}

    def _pages(
        self,
        path: str,
        params: list[tuple[str, str | int]],
        *,
        expired_on_404: bool = False,
    ) -> Iterator[dict[str, Any]]:
        token: str | None = None
        while True:
            paging = [("maxResults", _PAGE)] + ([("pageToken", token)] if token else [])
            page = self._get(path, params + paging, expired_on_404=expired_on_404)
            yield page
            token = page.get("nextPageToken")
            if not token:
                return

    def _get(
        self,
        path: str,
        params: list[tuple[str, str | int]] | None = None,
        *,
        expired_on_404: bool = False,
    ) -> dict[str, Any]:
        try:
            response = self._authorized().get(
                f"{_API}/{path}", params=params, timeout=_TIMEOUT_SECONDS
            )
        except RefreshError as error:
            raise CollectionError(
                "Gmail refused the token (expired or revoked): run pa gmail auth"
            ) from error
        except (requests.RequestException, GoogleAuthError) as error:
            raise CollectionError(f"couldn't reach Gmail ({type(error).__name__})") from error
        if response.status_code == 404 and expired_on_404:
            raise HistoryExpired
        if response.status_code in (401, 403):
            raise CollectionError(
                f"Gmail refused access (HTTP {response.status_code}): run pa gmail auth"
            )
        if response.status_code != 200:
            raise CollectionError(f"Gmail answered HTTP {response.status_code}")
        return response.json()

    def _authorized(self) -> AuthorizedSession:
        if self._session is None:
            require_private_token(self.token)
            try:
                credentials = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            except (ValueError, OSError) as error:
                raise CollectionError(
                    f"Gmail's token can't be read ({type(error).__name__}): run pa gmail auth"
                ) from error
            self._session = AuthorizedSession(credentials)
        return self._session


def require_private_token(token: Path) -> None:
    """Refuses a token that's missing or that anyone but its owner could read."""
    if not token.is_file():
        raise CollectionError("Gmail has no token yet: run pa gmail auth")
    if stat.S_IMODE(token.stat().st_mode) & 0o077:
        raise CollectionError("Gmail's token is readable by others: chmod 600 it")


def authorize(client_secrets: Path, token: Path, *, port: int = DEFAULT_AUTH_PORT) -> None:
    """Ask the owner's consent through a browser and save the token at `token`, mode 0600.

    The flow redirects to a loopback server on `port` on this machine. On a headless devbox,
    forward that port from the machine with the browser (`ssh -L <port>:localhost:<port>`),
    then open the printed address there.
    """
    if not client_secrets.is_file():
        raise CollectionError(
            "Gmail's OAuth client isn't there: download it from the Cloud project's "
            "credentials page (a Desktop app client)"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        bind_addr="127.0.0.1",
        port=port,
        open_browser=False,
        authorization_prompt_message=(
            f"Forward port {port} to this machine (ssh -L {port}:localhost:{port} <devbox>), "
            "then open this address in your browser:\n{url}"
        ),
        success_message="The PA can read your Gmail now. You can close this tab.",
        # Offline access with a fresh consent, so Google always hands back a refresh token.
        access_type="offline",
        prompt="consent",
    )
    token.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_atomically(token, credentials.to_json().encode(), mode=0o600)
