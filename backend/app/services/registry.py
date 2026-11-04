"""Picks the right `EmailProvider` for a connected mailbox.

Routers go through here rather than importing `GmailProvider`, so adding
Outlook means registering one class — no router changes. This is the seam the
architecture chapter claims exists.
"""

from sqlalchemy.orm import Session

from ..models import EmailAccount
from .gmail import GmailProvider
from .provider import EmailProvider, ProviderError

PROVIDERS: dict[str, type[EmailProvider]] = {
    "gmail": GmailProvider,
    # "outlook": OutlookProvider,  # implements the same five methods
}


def get_provider(db: Session, account: EmailAccount) -> EmailProvider:
    if account.provider == "demo":
        raise ProviderError(
            "This is the demo inbox — connect a real mailbox to use that feature."
        )
    provider_cls = PROVIDERS.get(account.provider)
    if provider_cls is None:
        raise ProviderError(f"No provider implementation for {account.provider!r}")
    return provider_cls(account, db=db)


def real_account(db: Session, user_id: int) -> EmailAccount:
    """The user's current non-demo mailbox, or a ProviderError if there isn't one.

    Most recently connected wins: if someone links a second mailbox, that is the
    one they mean. Taking whichever row came back first would let an abandoned
    account shadow the new one, and every sync would fail against dead
    credentials with no obvious way out.
    """
    account = (
        db.query(EmailAccount)
        .filter(EmailAccount.user_id == user_id, EmailAccount.provider != "demo")
        .order_by(EmailAccount.created_at.desc(), EmailAccount.id.desc())
        .first()
    )
    if not account:
        raise ProviderError("This needs a real mailbox — connect your Gmail account first.")
    return account
