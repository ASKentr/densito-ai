import os
import uuid

from app.config import settings


def new_study_storage_dir() -> tuple[str, str]:
    """Возвращает (relative_dir, absolute_dir) — уникальная папка для одного исследования."""
    rel = uuid.uuid4().hex
    abs_dir = os.path.join(settings.storage_dir, rel)
    os.makedirs(abs_dir, exist_ok=True)
    return rel, abs_dir


def make_display_id(seq: int) -> str:
    return f"DXA-{seq:06d}"
