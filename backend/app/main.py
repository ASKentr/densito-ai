from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from ai_module.violation_catalog import DEFAULT_VIOLATION_TYPES
from app.config import settings
from app.database import engine, init_db
from app.models import Role, User, ViolationType
from app.routers import admin, auth, batch, findings, studies
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
    # веб-интерфейс читает имя файла и счётчики пакетной обработки из ответа
    expose_headers=["Content-Disposition", "X-Processed-Files", "X-Failed-Files"],
)

app.include_router(auth.router)
app.include_router(studies.router)
app.include_router(findings.router)
app.include_router(admin.router)
app.include_router(batch.router)


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
    # прогрев: веса нейросети загружаются при старте, а не при первом анализе
    from ai_module.status import engine_status
    engine_status()


@app.get("/health")
def health():
    """Сервер жив + самопроверка ИИ-модуля (ai_module/status.py). Без авторизации:
    его опрашивает индикатор в шапке веб-интерфейса и проверка сборки в CI.
    status = "ok" — можно анализировать, "degraded" — сервер работает, ИИ-модуль нет."""
    from ai_module.status import engine_status

    ai = engine_status()
    return {"status": "ok" if ai["ready"] else "degraded", "service": settings.app_name, "ai": ai}
