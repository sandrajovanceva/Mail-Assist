"""Which messages are worth drafting a reply to.

These filters run before any model call, and every wrong "yes" costs a request
from a limited daily quota — so they are tested exhaustively.
"""

import pytest

from app.services.needs_reply import address_of, decide, is_no_reply_address

ME = "sandra@gmail.com"


def judge(**overrides):
    kwargs = {
        "sender": "Elena <elena@studio.co>",
        "category": "routine",
        "is_bulk": False,
        "last_thread_sender": None,
        "account_address": ME,
    }
    kwargs.update(overrides)
    return decide(**kwargs)


def test_a_personal_request_wants_a_reply():
    assert judge().needs_reply is True


@pytest.mark.parametrize(
    "sender",
    [
        "no-reply@anthropic.com",
        "noreply@anthropic.com",
        "no_reply@x.com",
        "donotreply@bank.mk",
        "do-not-reply@bank.mk",
        "notifications@github.com",
        "notification@x.com",
        "alerts@monitoring.io",
        "mailer-daemon@googlemail.com",
        "postmaster@x.com",
        "bounces@sendgrid.net",
        "Anthropic <no-reply-yrL7@mail.anthropic.com>",
    ],
)
def test_addresses_that_never_accept_replies(sender):
    assert is_no_reply_address(sender) is True
    assert judge(sender=sender).needs_reply is False


@pytest.mark.parametrize(
    "sender",
    [
        "elena@studio.co",
        "Noreply Jones <n.jones@company.com>",  # display name must not matter
        "replies@community.org",
        "support@company.com",
    ],
)
def test_real_people_are_not_mistaken_for_robots(sender):
    assert is_no_reply_address(sender) is False


def test_bulk_mail_is_skipped():
    """List-Unsubscribe / Precedence: bulk means it went to a list, not to you."""
    assert judge(is_bulk=True).needs_reply is False
    assert "bulk" in judge(is_bulk=True).reason


@pytest.mark.parametrize("category", ["newsletter", "spam"])
def test_newsletters_and_spam_are_skipped(category):
    assert judge(category=category).needs_reply is False


@pytest.mark.parametrize("category", ["important", "routine"])
def test_important_and_routine_are_candidates(category):
    assert judge(category=category).needs_reply is True


def test_a_thread_you_already_answered_is_skipped():
    decision = judge(last_thread_sender="Sandra <sandra@gmail.com>")
    assert decision.needs_reply is False
    assert "already replied" in decision.reason


def test_matching_your_address_ignores_case_and_display_name():
    assert judge(last_thread_sender='"Sandra J" <SANDRA@Gmail.com>').needs_reply is False


def test_a_thread_where_they_spoke_last_still_wants_a_reply():
    assert judge(last_thread_sender="Elena <elena@studio.co>").needs_reply is True


def test_unknown_thread_state_does_not_block_a_reply():
    """Better to offer an unnecessary draft than to miss a needed one."""
    assert judge(last_thread_sender=None).needs_reply is True


def test_unclassified_mail_is_still_a_candidate():
    """If classification failed, don't silently drop the message."""
    assert judge(category="").needs_reply is True


def test_address_extraction():
    assert address_of("Elena Petrova <elena@studio.co>") == "elena@studio.co"
    assert address_of("elena@studio.co") == "elena@studio.co"
    assert address_of("  <A@B.COM> ") == "a@b.com"


def test_every_rejection_explains_itself():
    """The UI shows this reason, so it must never be blank."""
    for decision in (
        judge(is_bulk=True),
        judge(sender="no-reply@x.com"),
        judge(category="spam"),
        judge(last_thread_sender=f"<{ME}>"),
    ):
        assert decision.needs_reply is False
        assert decision.reason
