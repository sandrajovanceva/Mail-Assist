"""Demo data so the UI can be shown without a connected Gmail or API key.
Also handy as a safety net for live thesis-defense demos."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    AppSettings,
    Draft,
    DraftEdit,
    EmailAccount,
    EmailMessage,
    KnowledgeItem,
    User,
)
from ..security import get_current_user

router = APIRouter(prefix="/dev", tags=["dev"])

DEMO_STYLE_PROFILE = """- Greetings: "Hi [first name]," for colleagues and clients; "Hey!" or "Yo" only with close friends
- Sign-offs: "Best regards," for formal mail, "Cheers," or just the first name for casual threads
- Sentences are short and direct; rarely more than two sentences per paragraph
- Gets to the point in the first line - no "I hope this email finds you well" openers
- Warm but efficient: often thanks the other person once, never repeatedly
- Uses concrete dates and times instead of vague phrasing ("Thursday at 14:00", not "later this week")
- Occasional light emoji with friends (🍜, 👍), never in professional mail
- Writes in English for work, mixes in Macedonian with close friends"""

DEMO_SIGNATURE = "Best regards,\nSandra Jovancheva"

DEMO_KNOWLEDGE = [
    {
        "kind": "template",
        "title": "Meeting decline (busy week)",
        "content": "Thanks for reaching out! This week is fully booked on my end - could we look at early next week instead? Happy to send a couple of slots that work.",
    },
    {
        "kind": "fact",
        "title": "Availability for calls",
        "content": "Prefers calls Tuesday-Thursday between 10:00 and 15:00 CET. No calls on Mondays (deep work day).",
    },
    {
        "kind": "template",
        "title": "Recruiter response (not looking)",
        "content": "Thanks for thinking of me! I'm not exploring new roles right now as I'm finishing my degree, but feel free to check back in around [month]. Happy to stay connected.",
    },
]

_now = lambda: datetime.now(timezone.utc)  # noqa: E731

MOCK_EMAILS = [
    {
        "sender": "Prof. Ana Kovačević <ana.kovacevic@university.edu>",
        "subject": "Thesis defense — proposed dates",
        "snippet": "The committee can meet on either Sep 14 or Sep 21. Please confirm which works for you and send your final draft at least one week before…",
        "body": "Hi,\n\nThe committee can meet on either September 14 or September 21 for your defense. Please confirm which date works for you, and remember to send the final draft of the thesis at least one week in advance.\n\nAlso attach the signed mentorship form when you reply.\n\nBest regards,\nProf. Ana Kovačević",
        "category": "important",
        "attention": True,
        "reason": "Direct request from thesis mentor requiring a personal decision about defense dates.",
        "hours_ago": 3,
        "summary": "Your mentor proposed two defense dates (Sep 14 or Sep 21) and needs your confirmation, the final thesis draft a week before, and the signed mentorship form.",
    },
    {
        "sender": "Marko Ilić <marko@carbonboxmedia.com>",
        "subject": "Client proposal for the Q4 campaign",
        "snippet": "Can you put together the revised offer for the Delta account by Thursday? They pushed back on pricing but liked the creative direction…",
        "body": "Hey,\n\nCan you put together the revised offer for the Delta account by Thursday? They pushed back on pricing but really liked the creative direction.\n\nIf we drop the video package to 3 deliverables we should land within their budget.\n\nMarko",
        "category": "important",
        "attention": True,
        "reason": "Work request with a hard deadline (Thursday) that needs the user's judgment on pricing.",
        "hours_ago": 7,
    },
    {
        "sender": "Elena Petrova <elena.petrova@studio-nova.co>",
        "subject": "Rescheduling tomorrow's sync",
        "snippet": "Something came up on my end — could we move our 10:00 call to 14:00, or push it to Friday morning instead?",
        "body": "Hi!\n\nSomething came up on my end — could we move our 10:00 call to 14:00, or push it to Friday morning instead? Whatever suits you better.\n\nSorry for the short notice!\nElena",
        "category": "routine",
        "attention": False,
        "reason": "Simple scheduling change; a short confirmation reply is enough.",
        "hours_ago": 22,
        "draft": {
            "content": "Hi Elena,\n\nNo problem at all — 14:00 tomorrow works for me, let's keep it on the same link.\n\nSee you then!\nAleksandar",
            "tone": "friendly",
            "status": "pending",
        },
        "read": True,
    },
    {
        "sender": "Stefan <stefan.j@gmail.com>",
        "subject": "Dinner on Saturday?",
        "snippet": "We're thinking that new ramen place around 8 — you in? Ivana and Luka are already confirmed.",
        "body": "Yo!\n\nWe're thinking that new ramen place on Saturday around 8 — you in? Ivana and Luka already confirmed.\n\nStefan",
        "category": "routine",
        "attention": False,
        "reason": "Casual social invitation; quick yes/no reply suffices.",
        "hours_ago": 30,
        "draft": {
            # The AI's version vs. what actually went out — seeds the
            # "what I learned from your edits" panel with a real example.
            "original": "Hi Stefan,\n\nThank you for the invitation! Saturday at 8 works well for me. I would be happy to join you, Ivana and Luka at the new ramen restaurant.\n\nBest regards,\nAleksandar",
            "content": "Count me in! Ramen at 8 sounds perfect — want me to book the table since I pass by there on Friday?\n\nSee you Saturday 🍜",
            "tone": "casual",
            "status": "sent",
        },
        "read": True,
    },
    {
        "sender": "TechCrunch <newsletter@techcrunch.com>",
        "subject": "Daily Crunch: The AI agents race heats up",
        "snippet": "Plus: a new unicorn in fintech, Apple's quiet ML acquisitions, and why every startup is suddenly an 'agent company'…",
        "body": "The AI agents race heats up\n\nEvery major lab shipped agent tooling this quarter. We break down what actually matters for builders...\n\n(unsubscribe | preferences)",
        "category": "newsletter",
        "attention": False,
        "reason": "Bulk newsletter content; no reply expected.",
        "hours_ago": 26,
    },
    {
        "sender": "University Registrar <registrar@university.edu>",
        "subject": "ACTION REQUIRED: Graduation paperwork deadline Sep 1",
        "snippet": "Our records show you have not yet submitted Form D-2 (diploma issuance request). The deadline for the autumn graduation cycle is September 1…",
        "body": "Dear student,\n\nOur records show you have not yet submitted Form D-2 (diploma issuance request). The deadline for the autumn graduation cycle is September 1.\n\nSubmissions after the deadline are processed in the spring cycle.\n\nUniversity Registrar's Office",
        "category": "important",
        "attention": True,
        "reason": "Administrative deadline with real consequences; the user must act personally.",
        "hours_ago": 49,
        "summary": "The registrar's office is missing your Form D-2 for autumn graduation; submitting after September 1 pushes your diploma to the spring cycle.",
    },
    {
        "sender": "Sofia Ricci <s.ricci@talentbridge.io>",
        "subject": "Junior ML engineer role — interested?",
        "snippet": "I came across your profile and thought you'd be a great fit for a junior ML engineer position at a Berlin-based startup (remote-friendly)…",
        "body": "Hi Aleksandar,\n\nI came across your profile and thought you'd be a great fit for a junior ML engineer position at a Berlin-based startup (remote-friendly, EUR 55-65k).\n\nWould you be open to a 20-minute intro call this week?\n\nSofia Ricci\nTalentBridge",
        "category": "routine",
        "attention": False,
        "reason": "Recruiter outreach; a polite templated response handles it.",
        "hours_ago": 55,
    },
    {
        "sender": "Crypto Rewards <win@crypt0-b0nus.xyz>",
        "subject": "You have 1.2 BTC waiting — claim within 24h!!!",
        "snippet": "Congratulations! Your wallet was selected for our loyalty airdrop. Verify your seed phrase now to claim…",
        "body": "Congratulations! Your wallet was selected. Verify your seed phrase at the link below to claim 1.2 BTC before it expires!!!",
        "category": "spam",
        "attention": False,
        "reason": "Classic phishing pattern: urgency, fake reward, and a request for wallet credentials.",
        "hours_ago": 60,
    },
    {
        "sender": "GitHub <notifications@github.com>",
        "subject": "[diplomska] Weekly digest: 12 commits, 2 issues closed",
        "snippet": "Your repositories had activity this week. diplomska: 12 commits on main. See what changed…",
        "body": "Weekly digest for your repositories:\n\ndiplomska — 12 commits, 2 issues closed\n\nView the full activity on GitHub.",
        "category": "newsletter",
        "attention": False,
        "reason": "Automated notification digest; informational only.",
        "hours_ago": 76,
    },
    {
        "sender": "Duolingo <hello@duolingo.com>",
        "subject": "Your 47-day streak is in danger! 🔥",
        "snippet": "You haven't practiced German today. Just 5 minutes keeps your streak alive…",
        "body": "You haven't practiced German today. Just 5 minutes keeps your 47-day streak alive!\n\n— Duo",
        "category": "newsletter",
        "attention": False,
        "reason": "Automated app reminder; no response needed.",
        "hours_ago": 80,
    },
]


@router.post("/seed")
def seed_demo(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = (
        db.query(EmailAccount).filter_by(user_id=user.id, provider="demo").first()
    )
    if not account:
        account = EmailAccount(
            user_id=user.id,
            provider="demo",
            email_address="demo@mailassist.app",
            credentials_json="{}",
        )
        db.add(account)
        db.flush()

    created = 0
    for i, m in enumerate(MOCK_EMAILS):
        provider_id = f"demo-{i}"
        if (
            db.query(EmailMessage)
            .filter_by(account_id=account.id, provider_message_id=provider_id)
            .first()
        ):
            continue
        email = EmailMessage(
            account_id=account.id,
            provider_message_id=provider_id,
            thread_id=f"demo-thread-{i}",
            sender=m["sender"],
            recipient="you@mailassist.app",
            subject=m["subject"],
            snippet=m["snippet"],
            body_text=m["body"],
            received_at=_now() - timedelta(hours=m["hours_ago"]),
            category=m["category"],
            needs_attention=m["attention"],
            classification_reason=m["reason"],
            summary=m.get("summary", ""),
            is_read=m.get("read", False),
        )
        db.add(email)
        db.flush()
        if "draft" in m:
            d = m["draft"]
            draft = Draft(
                email_id=email.id,
                content=d["content"],
                original_content=d.get("original", d["content"]),
                tone=d["tone"],
                status=d["status"],
                sent_at=_now() - timedelta(hours=m["hours_ago"] - 1)
                if d["status"] == "sent"
                else None,
            )
            db.add(draft)
            db.flush()
            if "original" in d:
                db.add(
                    DraftEdit(
                        user_id=user.id,
                        draft_id=draft.id,
                        subject=m["subject"],
                        original=d["original"],
                        edited=d["content"],
                    )
                )
        created += 1

    # ---- settings mock data (never overwrites what the user already wrote) ----
    row = db.query(AppSettings).filter_by(user_id=user.id).first()
    if not row:
        row = AppSettings(user_id=user.id, user_name=user.name)
        db.add(row)
    if not (row.style_profile or "").strip():
        row.style_profile = DEMO_STYLE_PROFILE
        row.demo_style_seeded = True
    if not (row.signature or "").strip():
        row.signature = DEMO_SIGNATURE
        row.demo_signature_seeded = True

    existing_titles = {
        k.title for k in db.query(KnowledgeItem).filter_by(user_id=user.id).all()
    }
    for item in DEMO_KNOWLEDGE:
        if item["title"] not in existing_titles:
            db.add(KnowledgeItem(user_id=user.id, demo_seeded=True, **item))

    db.commit()
    return {"created": created, "account": account.email_address}


@router.post("/clear")
def clear_demo(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    account = db.query(EmailAccount).filter_by(user_id=user.id, provider="demo").first()
    count = 0
    if account:
        emails = db.query(EmailMessage).filter_by(account_id=account.id).all()
        count = len(emails)
        for email in emails:
            for draft in db.query(Draft).filter_by(email_id=email.id).all():
                db.query(DraftEdit).filter_by(draft_id=draft.id).delete()
                db.delete(draft)
            db.delete(email)
        db.delete(account)

    # remove the seeded settings content (only if the user hasn't customized it)
    row = db.query(AppSettings).filter_by(user_id=user.id).first()
    if row:
        if row.demo_style_seeded and row.style_profile == DEMO_STYLE_PROFILE:
            row.style_profile = ""
        if row.demo_signature_seeded and row.signature == DEMO_SIGNATURE:
            row.signature = ""
        row.demo_style_seeded = False
        row.demo_signature_seeded = False
    for k in (
        db.query(KnowledgeItem)
        .filter(KnowledgeItem.user_id == user.id, KnowledgeItem.demo_seeded.is_(True))
        .all()
    ):
        original = next((item for item in DEMO_KNOWLEDGE if item["title"] == k.title), None)
        if original and k.kind == original["kind"] and k.content == original["content"]:
            db.delete(k)

    db.commit()
    return {"deleted": count}
