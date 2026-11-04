"""Decide which messages are worth drafting a reply to — before spending a
single model call.

Drafting is the expensive operation: a strong model, thousands of tokens, and
on a free-tier key a hard daily request cap. Deciding *whether* to draft is
almost free, because the answer is usually sitting in the message headers.

The filters here are deliberately conservative in one direction: they only ever
say "don't bother". Anything they are unsure about is passed on to the
classifier, which is the component qualified to judge content.
"""

import re
from dataclasses import dataclass

# Localparts that never want a human reply. Matched on the address, not the
# display name, so a person called "Noreply Jones" is unaffected.
# The trailing group matters: real senders append per-message tokens, e.g.
# `no-reply-yrL7pRJrrOpM@mail.anthropic.com`, so an exact match is not enough.
NO_REPLY_PATTERN = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|alerts?|mailer[-_.]?daemon"
    r"|postmaster|bounce[sd]?|automated|system|support[-_.]?bot)([-_.+].*)?$",
    re.IGNORECASE,
)

SKIP_CATEGORIES = {"newsletter", "spam"}


@dataclass
class Decision:
    needs_reply: bool
    reason: str


def address_of(sender: str) -> str:
    """'Name <a@b.com>' -> 'a@b.com'."""
    match = re.search(r"<([^>]+)>", sender)
    return (match.group(1) if match else sender).strip().lower()


def is_no_reply_address(sender: str) -> bool:
    address = address_of(sender)
    localpart = address.split("@", 1)[0] if "@" in address else address
    return bool(NO_REPLY_PATTERN.match(localpart))


def decide(
    *,
    sender: str,
    category: str,
    is_bulk: bool,
    last_thread_sender: str | None,
    account_address: str,
) -> Decision:
    """Should the assistant draft a reply to this message?

    `last_thread_sender` is the sender of the most recent message in the
    thread, or None when unknown. If that is the user, the conversation has
    already been answered — the single strongest signal available, and free.
    """
    if is_bulk:
        return Decision(False, "bulk mail (carries an unsubscribe header)")
    if is_no_reply_address(sender):
        return Decision(False, "sender does not accept replies")
    if category in SKIP_CATEGORIES:
        return Decision(False, f"classified as {category}")
    if last_thread_sender and address_of(last_thread_sender) == account_address.lower():
        return Decision(False, "you already replied in this thread")
    return Decision(True, "awaiting your reply")
