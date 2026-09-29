from sqlmodel import Session

from app.models import AuditLogEntry, User


def log_action(session: Session, user: User | None, action: str, entity: str = "",
                entity_id: int | None = None, details: str = "") -> None:
    entry = AuditLogEntry(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action,
        entity=entity,
        entity_id=entity_id,
        details=details,
    )
    session.add(entry)
    session.commit()
