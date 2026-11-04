"""Provider abstraction so Outlook/Yahoo can be added later without touching routers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FetchedEmail:
    provider_message_id: str
    thread_id: str
    sender: str
    recipient: str
    subject: str
    snippet: str
    body_text: str
    received_at: datetime
    message_id_header: str = ""
    is_read: bool = False
    # carries List-Unsubscribe / Precedence: bulk / Auto-Submitted — i.e. mail
    # that was sent to a list rather than to a person
    is_bulk: bool = False
    # sender of the newest message in this thread, when the provider can say
    # cheaply; used to skip conversations the user has already answered
    last_thread_sender: str | None = None


@dataclass
class ThreadMessage:
    sender: str
    date: str
    body_text: str


class ProviderError(RuntimeError):
    """The mailbox can't be used for this operation (demo inbox, unknown provider…)."""


class EmailProvider(ABC):
    """What every mail provider (Gmail, Outlook, ...) must implement."""

    @abstractmethod
    def fetch_recent(self, max_results: int = 20) -> list[FetchedEmail]:
        """Return the most recent inbox messages, newest first."""

    @abstractmethod
    def fetch_since(self, cursor: str) -> tuple[list[FetchedEmail], str] | None:
        """Return messages added since `cursor`, plus the new cursor.

        Returns None when the cursor is too old to be usable and the caller
        should fall back to `fetch_recent`.
        """

    @abstractmethod
    def current_cursor(self) -> str:
        """An opaque change cursor for the mailbox's current state."""

    @abstractmethod
    def fetch_sent_bodies(self, max_results: int = 30) -> list[str]:
        """Plain-text bodies of the user's recent sent mail (for style learning)."""

    @abstractmethod
    def fetch_thread(self, thread_id: str) -> list[ThreadMessage]:
        """Return all messages in a thread, oldest first."""

    def thread_last_sender(self, thread_id: str) -> str | None:
        """Sender of the newest message in the thread, or None if unknown.

        Not abstract: a provider that cannot answer cheaply should leave this
        alone rather than making an expensive call for a hint.
        """
        return None

    @abstractmethod
    def send_reply(
        self,
        to: str,
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str = "",
    ) -> str:
        """Send a reply; returns the provider's id of the sent message."""
