from sqlmodel import SQLModel, Session, create_engine
from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)

if settings.database_url.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys(dbapi_conn, _record):
        # SQLite по умолчанию не проверяет внешние ключи — включаем, чтобы нельзя
        # было оставить находки со ссылкой на удалённую запись (внешнее ревью, замечание 4)
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
