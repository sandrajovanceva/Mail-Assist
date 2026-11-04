"""HTML email -> text.

Marketing and transactional email is almost always HTML. Stripping tags alone
leaves the contents of <style> blocks behind, which then get stored, displayed,
and sent to the model on every classification — so this is a cost and quality
issue, not just a cosmetic one.
"""

import base64

from app.services.gmail import _decode_body, html_to_text


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


REAL_WORLD_HTML = """
<html>
<head>
  <style>
    @font-face {
      font-family: 'Anthropic Sans';
      src: url('https://assets.claude.ai/Fonts/AnthropicSans.otf') format('opentype');
      font-weight: 400;
    }
    .btn { background: #000; padding: 12px 24px; }
  </style>
</head>
<body>
  <!-- preheader -->
  <p>Sign in to Claude.ai</p>
  <p>Click the button below to finish signing in. This link expires in 10&nbsp;minutes.</p>
  <a class="btn" href="https://claude.ai/x">Sign in</a>
</body>
</html>
"""


def test_style_contents_are_removed():
    text = html_to_text(REAL_WORLD_HTML)
    assert "font-family" not in text
    assert "@font-face" not in text
    assert "assets.claude.ai" not in text
    assert "background: #000" not in text


def test_the_actual_message_survives():
    text = html_to_text(REAL_WORLD_HTML)
    assert "Sign in to Claude.ai" in text
    assert "finish signing in" in text


def test_script_and_comments_are_removed():
    text = html_to_text("<script>alert('x')</script><!-- hidden --><p>Hello</p>")
    assert "alert" not in text
    assert "hidden" not in text
    assert "Hello" in text


def test_entities_are_decoded():
    assert html_to_text("<p>Tom &amp; Jerry &lt;3</p>") == "Tom & Jerry <3"


def test_breaks_become_newlines():
    assert html_to_text("<p>one<br>two</p>") == "one\ntwo"


def test_whitespace_is_collapsed():
    text = html_to_text("<p>a</p>\n\n\n\n<p>b</p>")
    assert "\n\n\n" not in text


def test_plain_text_part_is_preferred_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": encode("The real message")}},
            {"mimeType": "text/html", "body": {"data": encode("<p>styled version</p>")}},
        ],
    }
    assert _decode_body(payload) == "The real message"


def test_html_is_used_when_there_is_no_plain_part():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": encode(REAL_WORLD_HTML)}},
        ],
    }
    body = _decode_body(payload)
    assert "Sign in to Claude.ai" in body
    assert "font-family" not in body


def test_an_empty_plain_part_falls_through_to_html():
    """Some senders include a blank text/plain part — don't return nothing."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": encode("   \n  ")}},
            {"mimeType": "text/html", "body": {"data": encode("<p>Real content</p>")}},
        ],
    }
    assert _decode_body(payload) == "Real content"


def test_nested_multipart_is_walked():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": encode("Nested body")}}
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "x"}},
        ],
    }
    assert _decode_body(payload) == "Nested body"


def test_message_with_no_text_returns_empty():
    assert _decode_body({"mimeType": "image/png", "body": {"attachmentId": "x"}}) == ""


# --- expired Gmail authorisation -------------------------------------------


def test_expired_refresh_token_gives_a_reconnect_message(monkeypatch):
    """Google expires refresh tokens weekly while the app is unverified.

    The raw `invalid_grant` tells the user nothing, so the provider must
    translate it into an instruction.
    """
    from google.auth.exceptions import RefreshError

    from app.models import EmailAccount
    from app.services import gmail
    from app.services.provider import ProviderError

    class ExpiredCreds:
        expired = True
        refresh_token = "stale"

        def refresh(self, request):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(gmail, "credentials_from_account", lambda account: ExpiredCreds())

    account = EmailAccount(provider="gmail", email_address="a@b.com", credentials_json="{}")
    try:
        gmail.GmailProvider(account)
    except ProviderError as exc:
        assert "Connect Gmail" in str(exc)
        assert "7 days" in str(exc)
    else:
        raise AssertionError("expected ProviderError")
