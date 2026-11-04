"""Re-run classification over emails that were stored without it.

Sync skips messages it has already seen, so mail imported while the Gemini key
was missing (or while the model was rate limited) stays unclassified forever.
This backfills those rows.

    python reclassify.py            # only unclassified mail
    python reclassify.py --all      # every email, e.g. after changing models
"""

import argparse
import sys
import time


def printable(text: str) -> str:
    """Windows consoles are cp1252 — emoji in a subject line would crash print()."""
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding)

from app.database import SessionLocal
from app.models import EmailAccount, EmailMessage
from app.services import llm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="re-classify everything")
    parser.add_argument("--sleep", type=float, default=1.0, help="pause between calls")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            db.query(EmailMessage)
            .join(EmailAccount)
            .filter(EmailAccount.provider != "demo")
            .order_by(EmailMessage.received_at.desc())
        )
        if not args.all:
            query = query.filter(EmailMessage.category == "")
        emails = query.all()

        if not emails:
            print("Nothing to classify.")
            return 0

        print(f"Classifying {len(emails)} email(s)\n")
        failed = 0
        for index, email in enumerate(emails, start=1):
            try:
                result = llm.classify_email(
                    email.sender,
                    email.subject,
                    email.body_text,
                    db=db,
                    user_id=email.account.user_id,
                )
            except llm.QuotaExceeded as exc:
                print(f"\nStopped at {index}/{len(emails)}: {exc}")
                print("Run again later — already-classified mail is skipped.")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  [{index}/{len(emails)}] FAILED {printable(email.subject[:40])!r}: {exc}")
                failed += 1
                continue

            email.category = result["category"]
            email.needs_attention = result["needs_attention"]
            email.classification_reason = result["reason"]
            db.commit()

            flag = " [needs you]" if email.needs_attention else ""
            print(f"  [{index}/{len(emails)}] {result['category']:10}{flag} {printable(email.subject[:48])!r}")
            if args.sleep:
                time.sleep(args.sleep)

        if failed:
            print(f"\n{failed} failed.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
