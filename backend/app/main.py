from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine, ensure_schema
from .routers import auth, dev, drafts, emails, settings as settings_router, usage
from .routers import calendar
from .routers import assistant

Base.metadata.create_all(bind=engine)
ensure_schema()  # add columns introduced after a database was first created

app = FastAPI(title="MailAssist", description="AI-powered email assistant (graduation thesis)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(emails.router)
app.include_router(drafts.router)
app.include_router(settings_router.router)
app.include_router(usage.router)
app.include_router(calendar.router)
app.include_router(assistant.router)
app.include_router(dev.router)


@app.get("/")
def health():
    return {"app": "MailAssist", "status": "ok"}
