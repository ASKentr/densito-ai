"""
REST API пакетной обработки тестового набора (ТЗ п.3.2): zip-архив (или один
DICOM) -> таблица результатов (ТЗ п.2.5) в .xlsx или .csv. Та же логика, что
CLI `batch_predict.py` (`ai_module/batch.py`), боевой движок — гибрид.

Исследования в БД веб-интерфейса не сохраняются: это отдельный сценарий
«прогнать набор и получить таблицу». Факт запуска пишется в журнал действий.

Пример:
    TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \\
        -d "username=expert&password=expert12345" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
    curl -X POST "http://localhost:8000/batch?format=xlsx" -H "Authorization: Bearer $TOKEN" \\
        -F "file=@test_set.zip" -o results.xlsx

С `sr=true` ответ — zip-архив (ТЗ п.2.7): таблица `densito_results.<format>` и
папка `sr/` с отчётами DICOM SR по каждому снимку (ТЗ п.2.6).
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from ai_module.batch import looks_like_dicom, result_zip_bytes, run_batch, table_bytes
from app.audit import log_action
from app.database import get_session
from app.models import User
from app.security import get_current_user
from app.storage import safe_upload_name

router = APIRouter(prefix="/batch", tags=["batch"])

_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


@router.post("", summary="Пакетная обработка: zip/DICOM -> таблица результатов (xlsx/csv)",
             response_class=Response,
             responses={200: {"description": "Таблица результатов (ТЗ п.2.5)",
                              "content": {_MEDIA_TYPES["xlsx"]: {}, "text/csv": {}, "application/zip": {}}}})
def batch_process(
    file: UploadFile = File(..., description="zip-архив с DICOM (папки и вложенные zip допускаются) или один DICOM-файл"),
    format: Literal["xlsx", "csv"] = Query("xlsx", description="Формат таблицы"),
    sr: bool = Query(False, description="Вернуть zip: таблица + отчёты DICOM SR по каждому снимку"),
    session: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    upload_name = safe_upload_name(file.filename)
    with tempfile.TemporaryDirectory(prefix="densito_upload_") as tmp:
        upload_path = Path(tmp) / upload_name
        with open(upload_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        if not zipfile.is_zipfile(upload_path) and not looks_like_dicom(upload_path):
            raise HTTPException(400, "Файл не распознан как zip-архив или DICOM")
        rows = run_batch(upload_path, "hybrid", archive_label=upload_name, with_sr=sr)

    if not rows:
        raise HTTPException(400, "В загруженных данных не найдено ни одного DICOM-файла")

    n_failed = sum(1 for r in rows if r["processing_status"] != "Success")
    log_action(session, user, "batch_process", entity="batch",
               details=f"{upload_name}: {len(rows)} файл(ов), Failure: {n_failed}")
    headers = {"X-Processed-Files": str(len(rows)), "X-Failed-Files": str(n_failed)}
    if sr:
        return Response(content=result_zip_bytes(rows, format), media_type="application/zip",
                        headers={**headers, "Content-Disposition": 'attachment; filename="densito_results.zip"'})
    return Response(
        content=table_bytes(rows, format),
        media_type=_MEDIA_TYPES[format],
        headers={**headers, "Content-Disposition": f'attachment; filename="densito_results.{format}"'},
    )
