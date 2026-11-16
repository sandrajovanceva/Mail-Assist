"""30-case synthetic style ablation, separate from the eight-case pilot."""
import sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import run_controlled_style as experiment

experiment.OUT = Path(__file__).parent/'final'/'style'
experiment.OUT.mkdir(exist_ok=True)
# The imported pilot module binds the pilot database; keep final telemetry separate.
experiment.engine = create_engine('sqlite:///'+(experiment.OUT/'evaluation.db').as_posix())
experiment.SessionLocal = sessionmaker(bind=experiment.engine)
# All incoming messages and reference replies below are synthetic. No user email
# or benchmark classifier output is used to construct these references.
experiment.TEST = [
 ('Maya','Could you send the presentation as a PDF instead of slides?','I will send the presentation as a PDF.'),
 ('Owen','The contract is attached. Can you confirm that you received it?','I received the contract. I will review it.'),
 ('Iris','Which file format do you need for the logo? You have not specified it yet.','Please send the available file formats. I will confirm which one we need.'),
 ('Noah','Can you commit to a budget before we send the final requirements?','I need the final requirements before confirming the budget.'),
 ('Lena','Our test failed. Would the error log help?','Please send the error log. I will review it.'),
 ('Eli','The updated mockup is ready. Shall I share the link?','Please send the link to the updated mockup. I will review it.'),
 ('Zoe','Do you want a written summary of the meeting?','Please send a written summary of the meeting.'),
 ('Theo','Please approve the revised invoice. It is not attached yet.','Please send the revised invoice. I need to review it before approving it.'),
 ('Aria','Can you estimate the migration time without seeing our current setup?','I need details of the current setup before estimating the migration time.'),
 ('Luca','We can provide a sample export. Would you like one?','Please send a sample export. I will check the format.'),
 ('Esme','Should I share the screenshots of the broken page?','Please send the screenshots. I will review them.'),
 ('Milo','Do you need the order number to check this issue?','Please send the order number. I will use it to check the issue.'),
 ('Nora','Can you confirm our delivery date? We have not agreed on one.','The delivery date still needs confirmation. Please send your preferred date.'),
 ('Finn','Are the draft requirements enough for a fixed quote? We expect changes.','I need the final requirements before providing a fixed quote.'),
 ('Isla','Would you like the spreadsheet as CSV or should I send both formats?','Please send both formats. I will check which one works best.'),
 ('Ezra','The notes are attached. Please acknowledge receipt.','I received the notes. I will review them.'),
 ('Vera','Would the sample report help you assess the layout?','Please send the sample report. I will review the layout.'),
 ('Aiden','Can we start before the access permissions are confirmed?','The access permissions need confirmation before we start.'),
 ('Cleo','Should I send the current checklist for your review?','Please send the current checklist. I will review it.'),
 ('Felix','We have two revisions of the brief. Do you want the latest version?','Please send the latest version of the brief.'),
 ('Freya','Can you give approval based on an old screenshot? The design has changed.','Please send the current design. I need to review it before approving it.'),
 ('Jude','We can reproduce the issue. Would you like the exact steps?','Please send the exact steps. I will use them to investigate.'),
 ('Mina','Would a short recording help explain the problem?','Please send a short recording. I will review it.'),
 ('Rory','Can you confirm that the final document arrived?','I received the final document. I will review it.'),
 ('Sara','The requirements are incomplete. Can you still promise a launch date?','I need the complete requirements before confirming a launch date.'),
 ('Amir','Do you want the proposed agenda before arranging the call?','Please send the proposed agenda. I will review it before we arrange the call.'),
 ('Dina','Should I include the reference number when sending the payment question?','Please include the reference number. I will use it to check the payment question.'),
 ('Leon','Would you like the updated timeline and the list of open questions?','Please send the updated timeline and the list of open questions. I will review both.'),
 ('Tessa','Can you approve this scope without reviewing the changes?','Please send the changes. I need to review them before approving the scope.'),
 ('Yara','The handover guide is ready. Would you like a copy?','Please send a copy of the handover guide. I will review it.'),
]

if __name__=='__main__':
    code=experiment.main()
    path=experiment.OUT/'STYLE_REPORT.md'
    if path.exists():
        text=path.read_text(encoding='utf-8').replace('eight disjoint synthetic test replies','30 disjoint synthetic test replies')
        path.write_text(text,encoding='utf-8')
    raise SystemExit(code)
