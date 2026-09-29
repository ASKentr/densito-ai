import os
import uuid
from typing import BinaryIO

from app.config import settings

# Лимит одного загружаемого файла (zip или DICOM); 413 при превышении.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "2048")) * 2**20


class UploadTooLarge(ValueError):
    pass


def save_upload(src: BinaryIO, path: str, limit: int | None = None) -> int:
    """Потоковая запись загрузки с лимитом размера; при превышении файл удаляется."""
    limit = MAX_UPLOAD_BYTES if limit is None else limit
    total = 0
    with open(path, "wb") as dst:
        while chunk := src.read(1 << 20):
            total += len(chunk)
            if total > limit:
                break
            dst.write(chunk)
    if total > limit:
        os.remove(path)
        raise UploadTooLarge(f"файл больше {limit // 2**20} МБ")
    return total


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
