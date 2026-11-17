"""Read-only access to the agents' shared mailbox, for JARVIS v2.

One mailbox only: guildensternai@gmail.com, the address shared by every agent
on guildenstern. The adapter checks which account its token belongs to before
the first read and refuses to work against any other.

Three limits are enforced here, in code, not left to the model:

- Read-only. The adapter only ever issues GET requests to Gmail. Nothing is
  sent, deleted, archived, labelled or marked read.
- One account. See ALLOWED_ACCOUNT.
- No nightly reports. The restaurant's end-of-day reports arrive in this
  mailbox and carry absolute sales and cover figures. Those reach JARVIS only
  through Atlas, as relative figures. Messages that look like a nightly report
  are left out of search results and cannot be opened.

Named gmail_inbox, not email or mailbox: both of those shadow stdlib modules.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from orchestrator.tools import ToolRegistry

ALLOWED_ACCOUNT = "guildensternai@gmail.com"

DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_TIMEOUT = 20.0

DEFAULT_RESULTS = 5
MAX_RESULTS = 10
MAX_BODY_CHARS = 8_000
MAX_QUERY_CHARS = 200

# Words that mark a restaurant nightly report. Matched as whole words, any
# case, against subject, preview and body.
DEFAULT_BLOCKED_TERMS = "EOD,SPLH"

# Gmail search terms the model may not use: they reach outside the inbox view
# this tool is meant to give (spam, trash) or cancel the report exclusion.
_FORBIDDEN_QUERY = re.compile(r"in:\s*(spam|trash|anywhere)", re.IGNORECASE)

_MESSAGE_ID = re.compile(r"^[0-9a-fA-F]{6,32}$")
_LIST_HEADERS = ("From", "Subject", "Date")


class EmailError(RuntimeError):
    """A mailbox request that could not be served. The message is user-safe."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def _decode(data: object) -> str:
    if not isinstance(data, str) or not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, UnicodeEncodeError):
        return ""


def _bodies(payload: object, mime_type: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    found: list[str] = []
    if payload.get("mimeType") == mime_type:
        body = payload.get("body")
        if isinstance(body, dict):
            text = _decode(body.get("data"))
            if text:
                found.append(text)
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            found.extend(_bodies(part, mime_type))
    return found


def message_text(payload: object) -> str:
    """The readable text of a message: plain parts, else HTML with tags removed."""
    plain = _bodies(payload, "text/plain")
    if plain:
        return "\n".join(plain).strip()
    extractor = _TextExtractor()
    for html in _bodies(payload, "text/html"):
        extractor.feed(html)
    return extractor.text()


def _header(payload: object, name: str) -> str:
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return ""
    for header in headers:
        if (
            isinstance(header, dict)
            and str(header.get("name", "")).lower() == name.lower()
        ):
            return str(header.get("value", ""))
    return ""


class GmailAdapter:
    """Read-only client for the one mailbox the agents share."""

    def __init__(
        self,
        token_file: str | None = None,
        blocked_terms: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        path = token_file or os.environ.get("EMAIL_TOKEN_FILE", "")
        if not path:
            raise ValueError("EMAIL_TOKEN_FILE is not configured")

        self._credentials = self._load_credentials(Path(path).expanduser())

        terms = (
            blocked_terms
            if blocked_terms is not None
            else os.environ.get("EMAIL_BLOCKED_TERMS") or DEFAULT_BLOCKED_TERMS
        )
        self.blocked_terms = [t.strip() for t in terms.split(",") if t.strip()]
        self._blocked = (
            re.compile(
                r"\b(" + "|".join(re.escape(t) for t in self.blocked_terms) + r")\b",
                re.IGNORECASE,
            )
            if self.blocked_terms
            else None
        )

        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._access_token = ""
        self._token_expires = 0.0
        self._account_checked = False

    @staticmethod
    def _load_credentials(path: Path) -> dict[str, str]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"EMAIL_TOKEN_FILE cannot be read: {exc}") from None
        except json.JSONDecodeError:
            raise ValueError("EMAIL_TOKEN_FILE is not valid JSON") from None
        if not isinstance(payload, dict):
            raise ValueError("EMAIL_TOKEN_FILE must hold a JSON object")

        credentials: dict[str, str] = {}
        for key in ("client_id", "client_secret", "refresh_token"):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"EMAIL_TOKEN_FILE is missing {key}")
            credentials[key] = value

        token_uri = payload.get("token_uri", DEFAULT_TOKEN_URI)
        if not isinstance(token_uri, str) or not token_uri.startswith("https://"):
            raise ValueError("EMAIL_TOKEN_FILE token_uri must be an HTTPS URL")
        credentials["token_uri"] = token_uri
        return credentials

    # ── transport ────────────────────────────────────────────────────────

    def _token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires:
            return self._access_token
        try:
            response = self.client.post(
                self._credentials["token_uri"],
                data={
                    "client_id": self._credentials["client_id"],
                    "client_secret": self._credentials["client_secret"],
                    "refresh_token": self._credentials["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
        except httpx.RequestError as exc:
            raise EmailError(f"Google sign-in is unreachable: {exc}") from None
        if response.status_code != 200:
            raise EmailError(
                f"Google refused the mailbox sign-in (HTTP {response.status_code}). "
                "The saved authorisation may have expired or been revoked."
            )
        body = response.json()
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise EmailError("Google sign-in returned no access token.")
        lifetime = body.get("expires_in", 3600)
        seconds = float(lifetime) if isinstance(lifetime, (int, float)) else 3600.0
        self._access_token = token
        self._token_expires = time.monotonic() + max(seconds - 60.0, 0.0)
        return token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # GET is the only verb this adapter has. Keep it that way: the
        # read-only promise in the module docstring rests on this method.
        try:
            response = self.client.get(
                f"{GMAIL_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        except httpx.RequestError as exc:
            raise EmailError(f"Gmail is unreachable: {exc}") from None
        if response.status_code == 404:
            raise EmailError("No message with that id in the mailbox.")
        if response.status_code != 200:
            raise EmailError(f"Gmail request failed (HTTP {response.status_code}).")
        return response.json()

    def _ensure_account(self) -> None:
        if self._account_checked:
            return
        profile = self._get("/profile")
        address = (
            str(profile.get("emailAddress", "")) if isinstance(profile, dict) else ""
        )
        if address.strip().lower() != ALLOWED_ACCOUNT:
            raise EmailError(
                "The saved mailbox authorisation is for a different account. "
                f"JARVIS reads {ALLOWED_ACCOUNT} only."
            )
        self._account_checked = True

    # ── the report exclusion ─────────────────────────────────────────────

    def _is_blocked(self, *texts: str) -> bool:
        if self._blocked is None:
            return False
        return any(self._blocked.search(text) for text in texts if text)

    def _full_query(self, query: str) -> str:
        cleaned = " ".join(query.split())
        if len(cleaned) > MAX_QUERY_CHARS:
            raise EmailError(f"Search is limited to {MAX_QUERY_CHARS} characters.")
        if _FORBIDDEN_QUERY.search(cleaned):
            raise EmailError("Search covers the mailbox only, not spam or trash.")
        if any(char in cleaned for char in "{}"):
            raise EmailError("Braces are not allowed in a mailbox search.")
        parts = [cleaned] if cleaned else []
        parts.append("-in:spam -in:trash")
        if self.blocked_terms:
            parts.append("-{" + " ".join(self.blocked_terms) + "}")
        return " ".join(parts)

    # ── tools ────────────────────────────────────────────────────────────

    def search(self, query: str = "", max_results: int = DEFAULT_RESULTS) -> Any:
        self._ensure_account()
        limit = max(1, min(int(max_results), MAX_RESULTS))
        listing = self._get(
            "/messages",
            {"q": self._full_query(query), "maxResults": str(limit)},
        )
        refs = listing.get("messages") if isinstance(listing, dict) else None

        messages: list[dict[str, Any]] = []
        withheld = 0
        for ref in refs if isinstance(refs, list) else []:
            message_id = ref.get("id") if isinstance(ref, dict) else None
            if not isinstance(message_id, str):
                continue
            detail = self._get(
                f"/messages/{message_id}",
                {"format": "metadata", "metadataHeaders": list(_LIST_HEADERS)},
            )
            payload = detail.get("payload")
            subject = _header(payload, "Subject")
            preview = str(detail.get("snippet", ""))
            if self._is_blocked(subject, preview):
                withheld += 1
                continue
            labels = detail.get("labelIds")
            messages.append(
                {
                    "id": message_id,
                    "from": _header(payload, "From"),
                    "subject": subject,
                    "date": _header(payload, "Date"),
                    "unread": isinstance(labels, list) and "UNREAD" in labels,
                    "preview": preview,
                }
            )

        result: dict[str, Any] = {
            "mailbox": ALLOWED_ACCOUNT,
            "count": len(messages),
            "messages": messages,
        }
        if withheld:
            result["withheld_nightly_reports"] = withheld
        return result

    def read(self, message_id: str) -> Any:
        if not _MESSAGE_ID.match(str(message_id)):
            raise EmailError("That is not a message id. Search first, then read by id.")
        self._ensure_account()
        detail = self._get(f"/messages/{message_id}", {"format": "full"})
        payload = detail.get("payload")
        subject = _header(payload, "Subject")
        body = message_text(payload)
        if self._is_blocked(subject, str(detail.get("snippet", "")), body):
            raise EmailError(
                "That message is a restaurant nightly report. Its figures are "
                "available through Atlas only, as relative changes."
            )
        truncated = len(body) > MAX_BODY_CHARS
        return {
            "mailbox": ALLOWED_ACCOUNT,
            "id": message_id,
            "from": _header(payload, "From"),
            "to": _header(payload, "To"),
            "subject": subject,
            "date": _header(payload, "Date"),
            "body": body[:MAX_BODY_CHARS],
            "truncated": truncated,
        }

    def register(self, tools: ToolRegistry) -> None:
        tools.register(
            name="email_search",
            description=(
                f"Search or list mail in {ALLOWED_ACCOUNT}, the mailbox shared "
                "by the agents on guildenstern. This is the only mailbox "
                "available; the user's personal and business mail is not. "
                "With no query it lists the newest messages. The query uses "
                "Gmail search syntax: from:, subject:, newer_than:7d, "
                "is:unread, plain words. Returns sender, subject, date, a "
                "short preview and an id for email_read. Restaurant nightly "
                "reports are left out — ask Atlas about those. Read-only: "
                "nothing is sent, deleted or marked read."
            ),
            schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query. Omit for newest mail.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_RESULTS,
                        "description": f"Default {DEFAULT_RESULTS}.",
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda query="", max_results=DEFAULT_RESULTS: self.search(
                query=query, max_results=max_results
            ),
        )

        tools.register(
            name="email_read",
            description=(
                f"Read one message from {ALLOWED_ACCOUNT} by the id that "
                "email_search returned. Returns sender, recipient, subject, "
                "date and the text of the body (long messages are cut "
                "short). Attachments are not read. Read-only; the message "
                "stays unread in Gmail."
            ),
            schema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The id from email_search.",
                    },
                },
                "required": ["message_id"],
                "additionalProperties": False,
            },
            handler=lambda message_id: self.read(message_id),
        )
