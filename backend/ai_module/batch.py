"""
Пакетная обработка DICOM -> таблица результатов (ТЗ п.2.5, 2.7). Общая логика
для CLI (`batch_predict.py`) и REST API (`POST /batch`, `app/routers/batch.py`),
чтобы две точки входа не расходились в результатах.

Вход: папка (ищется рекурсивно), zip-архив (в т.ч. вложенные zip) или один
файл. Одна строка = одно изображение (одна анатомическая область).

Колонки (порядок и названия — буквально из ТЗ, п.2.5, плюс quality_prob —
доп. колонка, разрешённая в «Разъяснения по вопросам ЛЦТ_V2.docx», вопрос 8):
  path_to_study, study_uid, image_uid, anatomical_region, quality_class,
  quality_prob, violation_type, processing_status, time_of_processing

Файл, который не удалось прочитать как валидный DICOM с читаемой анатомией,
получает processing_status="Failure" и пустые quality_class/violation_type —
по ТЗ п.2.7 такие файлы не должны приводить к необработанному исключению всего
пакета, но и не должны быть тихо посчитаны как "качественные". Причина ошибки
пишется в служебное поле `error` (в таблицу сдачи не попадает — лишние колонки
организатор не согласовывал; CLI выводит её отдельным файлом).
"""
from __future__ import annotations

import csv
import io
import tempfile
import time
import warnings
import zipfile
from pathlib import Path, PurePosixPath

import openpyxl
import pydicom

from ai_module.dicom_io import read_dicom
from ai_module.dicom_sr import SRContent, SRFinding, build_sr, sr_bytes
from ai_module.quality_prob import compute_quality_prob
from ai_module.submission_mapping import (
    HIP_VIOLATION_MAP, SPINE_VIOLATION_MAP, map_anatomical_region, map_violation_types,
)

COLUMNS = [
    "path_to_study", "study_uid", "image_uid", "anatomical_region",
    "quality_class", "quality_prob", "violation_type",
    "processing_status", "time_of_processing",
]

DICOM_EXTENSIONS = {".dcm", ".dicom"}
_SKIP_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
MAX_NESTED_ZIP_DEPTH = 3


def load_engine(name: str = "hybrid"):
    """hybrid — боевой (эвристики + CNN укладки бедра, нужен torch);
    heuristics — без torch (docs/ГИБРИД_МОДЕЛЬ.md)."""
    if name == "hybrid":
        from ai_module import hybrid  # импорт здесь: torch нужен только в этом режиме
        return hybrid
    if name == "heuristics":
        from ai_module import heuristics
        return heuristics
    raise ValueError(f"неизвестный движок: {name}")


def sr_findings(region: str, findings) -> list[SRFinding]:
    """Нарушения для DICOM SR — те же 5 официальных формулировок, что в
    violation_type, с пояснением алгоритма."""
    code_map = SPINE_VIOLATION_MAP if region == "spine" else HIP_VIOLATION_MAP if region == "hip" else {}
    out = []
    for code, official in code_map.items():
        details = [f.explanation for f in findings if f.violation_code == code and f.explanation]
        if any(f.violation_code == code for f in findings):
            out.append(SRFinding(official, " ".join(details)))
    return out


def process_file(path: Path, engine, with_sr: bool = False) -> dict:
    """Строка таблицы по одному файлу. with_sr=True — дополнительно поле `sr`
    (байты DICOM SR, ТЗ п.2.6) для успешно обработанных файлов."""
    row = {c: "" for c in COLUMNS}
    row["path_to_study"] = str(path)
    row["error"] = ""
    start = time.perf_counter()

    try:
        ds = read_dicom(str(path))
        row["study_uid"] = str(getattr(ds, "StudyInstanceUID", "") or "")
        row["image_uid"] = str(getattr(ds, "SOPInstanceUID", "") or "")

        report, findings = engine.analyze_image(0, str(path))
        region = report.anatomical_region

        if region not in ("spine", "hip"):
            # не удалось определить анатомическую область (нечитаемые пиксели,
            # неопределяемая структура и т.п.) — обработка технически
            # прошла, но результат по ТЗ-таксономии дать нельзя.
            row["processing_status"] = "Failure"
            row["error"] = "не удалось определить анатомическую область: " + "; ".join(
                f.explanation for f in findings) if findings else "не удалось определить анатомическую область"
            row["time_of_processing"] = round(time.perf_counter() - start, 4)
            return row

        row["anatomical_region"] = map_anatomical_region(region)
        violation_type = map_violation_types(region, [f.violation_code for f in findings])
        row["violation_type"] = violation_type
        row["quality_class"] = 1 if violation_type else 0
        row["quality_prob"] = round(compute_quality_prob(region, report.metrics, findings), 4)
        row["processing_status"] = "Success"
        if with_sr:
            row["sr"] = sr_bytes(build_sr(ds, SRContent(
                region=row["anatomical_region"], quality_class=row["quality_class"],
                quality_prob=row["quality_prob"], model_version=getattr(engine, "MODEL_VERSION", ""),
                findings=sr_findings(region, findings))))
    except Exception as exc:
        row["processing_status"] = "Failure"
        row["error"] = f"{type(exc).__name__}: {exc}"

    row["time_of_processing"] = round(time.perf_counter() - start, 4)
    return row


# ---------------------------------------------------------------- поиск файлов

def _skip(path: Path) -> bool:
    return (path.name.lower() in _SKIP_NAMES or path.name.startswith("._")
            or "__MACOSX" in path.parts)


def looks_like_dicom(path: Path) -> bool:
    """.dcm/.dicom — всегда (битый файл получит Failure, а не пропадёт молча);
    иначе — по сигнатуре DICM (Part 10) или, для файлов без расширения, по
    успешному чтению заголовка с тегами изображения."""
    if path.suffix.lower() in DICOM_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            if f.read(132)[128:132] == b"DICM":
                return True
    except OSError:
        return False
    if path.suffix == "":
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True, force=True)
            return "SOPInstanceUID" in ds and "Rows" in ds
        except Exception:
            return False
    return False


def find_dicom_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and not _skip(p) and looks_like_dicom(p))


# ---------------------------------------------------------------- zip

def _member_name(info: zipfile.ZipInfo) -> str:
    """Имена без флага UTF-8 zipfile декодирует как cp437, а русские архиваторы
    Windows пишут их в cp866 (так было в Датасет.zip) — перекодируем. Для
    ASCII-имён результат не меняется."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def extract_zip(zip_path: Path, dest: Path, depth: int = 0) -> None:
    """Распаковка с защитой от выхода за dest (`../`, абсолютные пути, диски)
    и с раскрытием вложенных zip (до MAX_NESTED_ZIP_DEPTH уровней)."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    nested: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            parts = [p for p in PurePosixPath(_member_name(info).replace("\\", "/")).parts
                     if p not in ("", ".", "..", "/") and not p.endswith(":")]
            if not parts:
                continue
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            if target.suffix.lower() == ".zip":
                nested.append(target)
    if depth < MAX_NESTED_ZIP_DEPTH:
        for inner in nested:
            if zipfile.is_zipfile(inner):
                extract_zip(inner, inner.with_suffix(""), depth + 1)
                inner.unlink()


# ---------------------------------------------------------------- прогон

def run_batch(input_path: str | Path, engine_name: str = "hybrid", archive_label: str | None = None,
              with_sr: bool = False) -> list[dict]:
    """Папка, zip-архив или один файл -> строки таблицы.
    Для zip `path_to_study` = `<имя архива>/<путь внутри архива>`.
    with_sr=True — у успешных строк поле `sr` с DICOM SR (см. process_file)."""
    warnings.filterwarnings("ignore")
    engine = load_engine(engine_name)
    input_path = Path(input_path)

    if input_path.is_file() and zipfile.is_zipfile(input_path):
        label = archive_label or input_path.name
        with tempfile.TemporaryDirectory(prefix="densito_batch_") as tmp:
            tmp_root = Path(tmp)
            extract_zip(input_path, tmp_root)
            rows = []
            for p in find_dicom_files(tmp_root):
                row = process_file(p, engine, with_sr)
                row["path_to_study"] = f"{label}/{p.relative_to(tmp_root).as_posix()}"
                rows.append(row)
            return rows
    if input_path.is_dir():
        return [process_file(p, engine, with_sr) for p in find_dicom_files(input_path)]
    if input_path.is_file():
        row = process_file(input_path, engine, with_sr)
        if archive_label:
            row["path_to_study"] = archive_label
        return [row]
    raise FileNotFoundError(f"нет такого файла или папки: {input_path}")


# ---------------------------------------------------------------- вывод

def table_bytes(rows: list[dict], fmt: str) -> bytes:
    """Таблица сдачи в памяти: fmt = "csv" (UTF-8 с BOM — корректно
    открывается в Excel) или "xlsx"."""
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8-sig")
    if fmt == "xlsx":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(COLUMNS)
        for row in rows:
            ws.append([row[c] for c in COLUMNS])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    raise ValueError(f"неизвестный формат: {fmt}")


def write_table(rows: list[dict], out_path: str | Path) -> None:
    fmt = "csv" if str(out_path).lower().endswith(".csv") else "xlsx"
    Path(out_path).write_bytes(table_bytes(rows, fmt))


# ---------------------------------------------------------------- DICOM SR

def sr_member_name(row: dict) -> str:
    """Путь SR-файла внутри архива/папки отчётов: повторяет path_to_study
    (без диска и `..`), к имени добавляется `.sr.dcm`."""
    parts = [p for p in PurePosixPath(row["path_to_study"].replace("\\", "/")).parts
             if p not in ("", ".", "..", "/") and not p.endswith(":")]
    return "/".join(parts or ["image"]) + ".sr.dcm"


def write_sr_files(rows: list[dict], out_dir: str | Path) -> int:
    out_dir = Path(out_dir)
    n = 0
    for row in rows:
        if row.get("sr"):
            target = out_dir / sr_member_name(row)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(row["sr"])
            n += 1
    return n


def result_zip_bytes(rows: list[dict], fmt: str) -> bytes:
    """ТЗ п.2.7: общая таблица + zip с дополнительными сериями. В архиве —
    `densito_results.<fmt>` и папка `sr/` с отчётами DICOM SR."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"densito_results.{fmt}", table_bytes(rows, fmt))
        for row in rows:
            if row.get("sr"):
                zf.writestr("sr/" + sr_member_name(row), row["sr"])
    return buf.getvalue()
