import os
import uuid

from app.config import settings


def new_study_storage_dir() -> tuple[str, str]:
    """Возвращает (relative_dir, absolute_dir) — уникальная папка для одного исследования."""
    rel = uuid.uuid4().hex
    abs_dir = os.path.join(settings.storage_dir, rel)
    os.makedirs(abs_dir, exist_ok=True)
    return rel, abs_dir


def safe_upload_name(filename: str | None) -> str:
    """Имя файла из запроса -> только базовое имя, без каталогов.
    Иначе имя вида `../../x` записало бы файл вне папки исследования
    (path traversal). Разделители обоих видов: клиент может быть на Windows."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name in ("", ".", ".."):
        return "upload.bin"
    return name


def make_display_id(seq: int) -> str:
    return f"DXA-{seq:06d}"
