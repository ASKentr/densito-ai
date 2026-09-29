from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from ai_module.violation_catalog import DEFAULT_VIOLATION_TYPES
from app.config import settings
from app.database import engine, init_db
from app.models import Role, User, ViolationType
from app.routers import admin, auth, findings, studies
from app.security import hash_password

app = FastAPI(title=settings.app_name, description=(
    "AI-сервис оценки качества денситометрических исследований (DXA): "
    "загрузка DICOM -> анализ качества и разметки -> экспертная проверка."
))

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(studies.router)
app.include_router(findings.router)
app.include_router(admin.router)


def _seed():
    with Session(engine) as session:
        if not session.exec(select(ViolationType)).first():
            for item in DEFAULT_VIOLATION_TYPES:
                session.add(ViolationType(**item))
            session.commit()

        if not session.exec(select(User)).first():
            session.add(User(
                username=settings.bootstrap_admin_username,
                full_name="Администратор",
                role=Role.admin,
                password_hash=hash_password(settings.bootstrap_admin_password),
            ))
            session.add(User(
                username=settings.bootstrap_expert_username,
                full_name="Эксперт-рентгенолог",
                role=Role.expert,
                password_hash=hash_password(settings.bootstrap_expert_password),
            ))
            session.commit()


@app.on_event("startup")
def on_startup():
    init_db()
    _seed()


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}
