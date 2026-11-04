import base64
import json
import logging
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html import unescape

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import EmailAccount
from .provider import EmailProvider, FetchedEmail, ProviderError, ThreadMessage

log = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"


def credentials_from_account(account: EmailAccount) -> Credentials:
    data = json.loads(account.credentials_json)
    expiry = None
    if data.get("expiry"):
        # google-auth compares this against naive UTC
        expiry = datetime.fromisoformat(data["expiry"]).replace(tzinfo=None)
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=data.get("scopes") or GMAIL_SCOPES,
        expiry=expiry,
    )


def credentials_to_json(creds: Credentials) -> str:
    return json.dumps(
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
            # what the user actually granted — they may have unticked "send"
            "scopes": list(creds.granted_scopes if creds.granted_scopes is not None else (creds.scopes or [])),
        }
    )


def granted_scopes(account: EmailAccount) -> list[str]:
    try:
        return json.loads(account.credentials_json).get("scopes") or []
    except (ValueError, AttributeError):
        return []


def can_send(account: EmailAccount) -> bool:
    scopes = granted_scopes(account)
    # no recorded scopes = connected before we started storing them; assume yes
    return not scopes or "https://www.googleapis.com/auth/gmail.send" in scopes


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def html_to_text(html: str) -> str:
    """Reduce an HTML email to readable text.

    Removing tags alone is not enough: the *contents* of <style> and <script>
    survive it, so a marketing email arrives as a wall of CSS. That junk then
    gets stored, shown to the user, and — worst of all — sent to the model on
    every classification, where it costs tokens and buries the actual message.
    """
    html = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    # keep the line structure the reader would see
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|table|blockquote)>", "\n", html)

    text = unescape(re.sub(r"<[^>]+>", " ", html))
    text = text.replace(" ", " ").replace("‌", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _collect_parts(payload: dict, found: dict[str, str]) -> None:
    """Depth-first walk recording the first text/plain and text/html bodies."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime in ("text/plain", "text/html") and mime not in found:
        found[mime] = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        _collect_parts(part, found)


def _decode_body(payload: dict) -> str:
    """Best-effort plain text for a message, preferring a real text/plain part."""
    found: dict[str, str] = {}
    _collect_parts(payload, found)
    if found.get("text/plain", "").strip():
        return found["text/plain"].strip()
    if found.get("text/html"):
        return html_to_text(found["text/html"])
    return ""


class GmailProvider(EmailProvider):
    def __init__(self, account: EmailAccount, db: Session | None = None):
        self.account = account
        self.db = db
        creds = credentials_from_account(account)

        # Refresh up front and write the new access token back, so the next
        # request doesn't have to refresh all over again.
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # Google expires refresh tokens after 7 days while the OAuth
                # consent screen is in "Testing" status, and the user can also
                # revoke access at any time. Say so plainly — the raw
                # `invalid_grant` tells the user nothing about what to do.
                raise ProviderError(
                    "Your Gmail connection has expired — click “Connect Gmail” to "
                    "reconnect. (While the app is unverified, Google expires access "
                    "every 7 days.)"
                ) from exc
            self._persist(creds)

        self.service = build("gmail", "v1", credentials=creds)

    def _persist(self, creds: Credentials) -> None:
        if self.db is None:
            return
        self.account.credentials_json = credentials_to_json(creds)
        self.db.commit()

    # ---------- reading ----------

    @staticmethod
    def _is_bulk(headers: list[dict]) -> bool:
        """Mail sent to a list rather than to a person — free to detect."""
        if _header(headers, "List-Unsubscribe") or _header(headers, "List-Id"):
            return True
        if _header(headers, "Precedence").lower() in ("bulk", "list", "junk"):
            return True
        return _header(headers, "Auto-Submitted").lower().startswith("auto")

    def _to_email(self, msg: dict) -> FetchedEmail:
        headers = msg["payload"].get("headers", [])
        internal_ms = int(msg.get("internalDate", "0"))
        labels = msg.get("labelIds", []) or []
        return FetchedEmail(
            is_bulk=self._is_bulk(headers),
            provider_message_id=msg["id"],
            thread_id=msg.get("threadId", ""),
            message_id_header=_header(headers, "Message-ID"),
            sender=_header(headers, "From"),
            recipient=_header(headers, "To"),
            subject=_header(headers, "Subject"),
            snippet=msg.get("snippet", ""),
            body_text=_decode_body(msg["payload"])[:20000],
            received_at=datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc),
            is_read="UNREAD" not in labels,
        )

    def _get_message(self, message_id: str) -> dict:
        return (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def fetch_recent(self, max_results: int = 20) -> list[FetchedEmail]:
        resp = (
            self.service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], maxResults=max_results)
            .execute()
        )
        return [self._to_email(self._get_message(ref["id"])) for ref in resp.get("messages", [])]

    def current_cursor(self) -> str:
        profile = self.service.users().getProfile(userId="me").execute()
        return str(profile.get("historyId", ""))

    def fetch_since(self, cursor: str) -> tuple[list[FetchedEmail], str] | None:
        """Only the messages added since `cursor`, using Gmail's history feed.

        Returns None if Gmail has expired the cursor (history is kept for a
        limited window), telling the caller to do a full fetch instead.
        """
        if not cursor:
            return None
        message_ids: list[str] = []
        page_token = None
        latest = cursor
        try:
            while True:
                resp = (
                    self.service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=cursor,
                        # labelAdded matters as much as messageAdded: rescuing a
                        # message from Spam, or filing one into the inbox later,
                        # adds the INBOX label without re-adding the message.
                        historyTypes=["messageAdded", "labelAdded"],
                        labelId="INBOX",
                        pageToken=page_token,
                    )
                    .execute()
                )
                for record in resp.get("history", []):
                    for added in record.get("messagesAdded", []):
                        message_ids.append(added["message"]["id"])
                    for labelled in record.get("labelsAdded", []):
                        if "INBOX" in (labelled.get("labelIds") or []):
                            message_ids.append(labelled["message"]["id"])
                latest = str(resp.get("historyId", latest))
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            if exc.resp.status == 404:  # cursor too old — caller does a full sync
                log.info("Gmail history cursor expired; falling back to full fetch")
                return None
            raise

        emails = []
        for message_id in dict.fromkeys(message_ids):  # de-dupe, keep order
            try:
                msg = self._get_message(message_id)
            except HttpError as exc:
                if exc.resp.status == 404:
                    continue  # deleted between the history call and now
                raise
            # history can surface a message that has since moved to spam or
            # trash; only the inbox is ours to process
            if "INBOX" not in (msg.get("labelIds") or []):
                continue
            emails.append(self._to_email(msg))
        emails.sort(key=lambda e: e.received_at, reverse=True)
        return emails, latest

    def fetch_sent_bodies(self, max_results: int = 30) -> list[str]:
        """Plain-text bodies of the user's most recent sent emails (for style learning)."""
        resp = (
            self.service.users()
            .messages()
            .list(userId="me", labelIds=["SENT"], maxResults=max_results)
            .execute()
        )
        bodies: list[str] = []
        for ref in resp.get("messages", []):
            body = _decode_body(self._get_message(ref["id"])["payload"]).strip()
            if body:
                bodies.append(body[:3000])
        return bodies

    def fetch_thread(self, thread_id: str) -> list[ThreadMessage]:
        thread = (
            self.service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        out: list[ThreadMessage] = []
        for msg in thread.get("messages", []):
            headers = msg["payload"].get("headers", [])
            out.append(
                ThreadMessage(
                    sender=_header(headers, "From"),
                    date=_header(headers, "Date"),
                    body_text=_decode_body(msg["payload"])[:10000],
                )
            )
        return out

    def thread_last_sender(self, thread_id: str) -> str | None:
        """Who sent the newest message in this thread.

        Uses `metadata` format and asks for one header, so Gmail returns a few
        hundred bytes instead of the whole conversation.
        """
        try:
            thread = (
                self.service.users()
                .threads()
                .get(
                    userId="me",
                    id=thread_id,
                    format="metadata",
                    metadataHeaders=["From"],
                )
                .execute()
            )
        except HttpError as exc:
            log.info("Could not read thread %s: %s", thread_id, exc)
            return None
        messages = thread.get("messages", [])
        if not messages:
            return None
        return _header(messages[-1]["payload"].get("headers", []), "From") or None

    # ---------- sending ----------

    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str = "",
    ) -> str:
        if not can_send(self.account):
            raise ProviderError(
                "This mailbox was connected with read-only access. Reconnect Gmail "
                "and tick the 'send email' permission to send replies."
            )
        mime = MIMEText(body)
        mime["to"] = to
        mime["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if in_reply_to:
            # Gmail's threadId keeps it together in *our* mailbox; these headers
            # are what make it a reply in the recipient's client.
            mime["In-Reply-To"] = in_reply_to
            mime["References"] = in_reply_to
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        payload: dict = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        sent = self.service.users().messages().send(userId="me", body=payload).execute()
        return sent.get("id", "")
