"""Does the style profile actually make drafts sound more like the user?

    python eval/style_eval.py --judge

Runs an ablation: for each example the drafter is asked for a reply twice —
once with the user's style profile and once without it, holding identity,
signature, tone and knowledge constant — and both are compared against the reply the user really sent.

Metrics per draft:
  * length ratio        — drafted words / real words (1.0 is perfect)
  * sentence length     — |avg sentence length difference| in words
  * vocabulary overlap  — Jaccard similarity of the word sets
  * greeting / sign-off — does it open and close the way the user does
  * judge score         — optional 1-5 rating from the model itself (--judge)

Only the ablation comparison is meaningful on its own: none of these numbers is
a "good writing" score, they measure *similarity to this person's writing*.
"""

import sys

# Windows consoles default to cp1252; eval output contains arrows, emoji and
# Cyrillic subject lines, so force UTF-8 rather than crashing on a print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services import llm  # noqa: E402

RESULTS_DIR = Path(__file__).parent / "results"


def words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+(?:['?][^\W\d_]+)*", text.casefold(), re.UNICODE)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[.!?\n]+", text) if s.strip()]


def avg_sentence_length(text: str) -> float:
    parts = sentences(text)
    return statistics.mean(len(words(s)) for s in parts) if parts else 0.0


def first_line(text: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip().lower()
    return ""


def last_line(text: str) -> str:
    lines = [line.strip().lower() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def compare(draft: str, real: str) -> dict:
    draft_words, real_words = words(draft), words(real)
    overlap = (
        len(set(draft_words) & set(real_words)) / len(set(draft_words) | set(real_words))
        if draft_words or real_words
        else 0.0
    )
    return {
        "length_ratio": len(draft_words) / len(real_words) if real_words else 0.0,
        "sentence_length_delta": abs(avg_sentence_length(draft) - avg_sentence_length(real)),
        "vocabulary_overlap": overlap,
        "greeting_match": first_line(draft)[:12] == first_line(real)[:12],
        "signoff_match": last_line(draft)[:12] == last_line(real)[:12],
    }


JUDGE_SYSTEM = (
    "You compare two email replies to the same message: one written by a person, one "
    "drafted by an AI trying to imitate that person. Rate how convincingly the AI draft "
    "matches the person's voice — tone, formality, length, greetings and sign-offs, "
    "directness — on a scale of 1 (obviously different) to 5 (indistinguishable). "
    "Judge the voice, not whether the content is better. Reply with the number alone."
)


def judge(draft: str, real: str, db) -> float | None:
    """Ask the model to rate voice similarity. Crude, but it correlates with
    human judgement better than the lexical metrics alone — report both."""
    try:
        text = llm._generate(
            operation="judge",
            model=settings.drafter_model,
            system=JUDGE_SYSTEM,
            prompt=f"--- Written by the person ---\n{real}\n\n--- AI draft ---\n{draft}",
            max_output_tokens=16,
            db=db,
            user_id=None,
        )
        match = re.search(r"[1-5]", text)
        return float(match.group()) if match else None
    except Exception as exc:  # noqa: BLE001
        print(f"    judge failed: {exc}")
        return None


def summarise(rows: list[dict], label: str) -> str:
    def mean(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return statistics.mean(values) if values else 0.0

    scores = [r["judge_score"] for r in rows if r.get("judge_score") is not None]
    line = (
        f"| {label} | {mean('length_ratio'):.2f} | {mean('sentence_length_delta'):.1f} | "
        f"{mean('vocabulary_overlap'):.3f} | "
        f"{sum(r['greeting_match'] for r in rows)}/{len(rows)} | "
        f"{sum(r['signoff_match'] for r in rows)}/{len(rows)} | "
    )
    line += f"{statistics.mean(scores):.2f} |" if scores else "— |"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=settings.drafter_model)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "style_dataset.jsonl")
    parser.add_argument("--judge", action="store_true", help="also ask the model to rate voice match")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"No dataset at {args.dataset}.")
        print("Copy style_dataset.example.jsonl and fill it with your own sent replies.")
        return 1

    settings.drafter_model = args.model
    RESULTS_DIR.mkdir(exist_ok=True)

    with args.dataset.open(encoding="utf-8") as handle:
        dataset = [json.loads(line) for line in handle if line.strip()]
    if args.limit:
        dataset = dataset[: args.limit]

    profile_path = Path(__file__).parent / "style_profile.txt"
    style_profile = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    if not style_profile.strip():
        print(f"A non-empty {profile_path.name} is required for a style ablation.")
        return 1

    provenance = profile_path.with_suffix(".sources.json")
    if not provenance.exists():
        print(f"Provide {provenance.name}: a JSON array of profile-training example IDs, separate from this evaluation dataset.")
        return 1
    training_ids = set(json.loads(provenance.read_text(encoding="utf-8")))
    if not training_ids or training_ids.intersection(row["id"] for row in dataset):
        print("Profile training IDs must be non-empty and disjoint from evaluation IDs.")
        return 1

    with_profile: list[dict] = []
    without_profile: list[dict] = []
    db = SessionLocal()
    try:
        for index, row in enumerate(dataset, start=1):
            email = SimpleNamespace(
                sender=row["incoming_sender"],
                subject=row["incoming_subject"],
                body_text=row["incoming_body"],
            )
            print(f"[{index}/{len(dataset)}] {row['id']}")

            for label, app_settings, bucket in (
                ("with", SimpleNamespace(user_name=row.get("user_name", ""), signature=row.get("signature", ""), style_profile=style_profile), with_profile),
                ("without", SimpleNamespace(user_name=row.get("user_name", ""), signature=row.get("signature", ""), style_profile=""), without_profile),
            ):
                try:
                    draft = llm.draft_reply(
                        email,
                        row.get("tone", "professional"),
                        app_settings,
                        [],
                        db=db,
                        user_id=None,
                    )
                except llm.QuotaExceeded as exc:
                    print(f"\nStopped: {exc}")
                    return 1
                metrics = compare(draft, row["real_reply"])
                metrics["id"] = row["id"]
                metrics["draft"] = draft
                if args.judge:
                    metrics["judge_score"] = judge(draft, row["real_reply"], db)
                bucket.append(metrics)
                print(f"    {label:8} overlap={metrics['vocabulary_overlap']:.3f} "
                      f"len_ratio={metrics['length_ratio']:.2f}")
    finally:
        db.close()

    report = [
        f"# Style matching — `{args.model}`\n",
        f"{len(dataset)} examples. 'With profile' injects the learned style guide; "
        "Both arms use the same identity, signature, tone and knowledge; only the style guide differs.\n",
        "| Arm | Length ratio | Sent. len. Δ | Vocab overlap | Greeting | Sign-off | Judge |",
        "|---|---:|---:|---:|---:|---:|---:|",
        summarise(with_profile, "With profile"),
        summarise(without_profile, "Without profile"),
    ]
    text = "\n".join(report)
    out = RESULTS_DIR / f"style-{args.model}.md"
    out.write_text(text, encoding="utf-8")
    (RESULTS_DIR / f"style-{args.model}-drafts.json").write_text(
        json.dumps({"with": with_profile, "without": without_profile}, indent=2),
        encoding="utf-8",
    )
    print("\n" + text)
    print(f"\nWritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
