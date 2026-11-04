"""Pull emails out of the connected mailbox into a spreadsheet to label.

    python eval/export_for_labelling.py --limit 150

Writes `eval/to_label.csv`, which opens in Excel. Two columns are left empty
for you to fill in:

    category         important | routine | newsletter | spam
    needs_attention  yes | no

Deliberately does NOT include what the system predicted. Seeing the model's
guess first biases the labeller towards agreeing with it, which would quietly
inflate the measured accuracy.

When the spreadsheet is filled in, run `python eval/import_labels.py`.
"""

import argparse
import csv
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models import EmailAccount, EmailMessage  # noqa: E402
from app.services.provider import ProviderError  # noqa: E402
from app.services.registry import get_provider  # noqa: E402

OUT = Path(__file__).parent / "to_label.csv"
COLUMNS = ["id", "sender", "subject", "body", "category", "needs_attention"]


def from_mailbox(db, limit: int) -> list[dict]:
    """Fetch straight from Gmail — more mail than the app has synced."""
    account = (
        db.query(EmailAccount).filter(EmailAccount.provider != "demo").first()
    )
    if not account:
        raise ProviderError("No mailbox connected. Connect one in the app first.")
    provider = get_provider(db, account)
    print(f"Fetching up to {limit} messages from {account.email_address}…")
    return [
        {
            "id": f"m-{i:03d}",
            "sender": e.sender,
            "subject": e.subject,
            "body": e.body_text[:3000],
            "category": "",
            "needs_attention": "",
        }
        for i, e in enumerate(provider.fetch_recent(max_results=limit), start=1)
    ]


def from_database(db, limit: int) -> list[dict]:
    """Fall back to what the app has already stored."""
    rows = (
        db.query(EmailMessage)
        .join(EmailAccount)
        .filter(EmailAccount.provider != "demo")
        .order_by(EmailMessage.received_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": f"m-{i:03d}",
            "sender": e.sender,
            "subject": e.subject,
            "body": e.body_text[:3000],
            "category": "",
            "needs_attention": "",
        }
        for i, e in enumerate(rows, start=1)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="use already-synced mail instead of fetching from the provider",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = from_database(db, args.limit) if args.from_db else from_mailbox(db, args.limit)
    except ProviderError as exc:
        print(f"{exc}\nTip: --from-db exports whatever the app has already synced.")
        return 1
    finally:
        db.close()

    if not rows:
        print("No emails found.")
        return 1

    # utf-8-sig so Excel on Windows shows Cyrillic correctly
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUT}")
    print("\nNext:")
    print("  1. Open to_label.csv in Excel")
    print("  2. Fill the 'category' column: important / routine / newsletter / spam")
    print("  3. Fill 'needs_attention': yes / no")
    print("  4. Save as CSV, then run: python eval/import_labels.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
