"""
Пакетный прогон DICOM -> таблица результатов (ТЗ п.2.5, 2.7) — CLI.
Логика — `ai_module/batch.py` (её же использует REST API `POST /batch`).

    python batch_predict.py <папка | архив.zip | файл.dcm> <результат.xlsx|.csv> [--engine hybrid|heuristics] [--sr ПАПКА]

Вход: папка (DICOM ищутся рекурсивно, в т.ч. файлы без расширения — по
сигнатуре), zip-архив (включая вложенные zip и имена в cp866) или один файл.

По умолчанию — гибрид `hybrid.py`: эвристики + CNN для укладки бедра (решение
команды 2026-09-24, см. `docs/ГИБРИД_МОДЕЛЬ.md` и `docs/КАЧЕСТВО_РЕШЕНИЯ.md`;
нужны зависимости из `requirements-model.txt` и веса в `ai_module/weights/`).
Чистые эвристики — флаг `--engine heuristics`.

`--sr ПАПКА` — дополнительно сохранить по каждому успешно обработанному
снимку текстовый отчёт DICOM SR (ТЗ п.2.6); структура папок повторяет
path_to_study, к имени добавляется `.sr.dcm`.

Если какие-то файлы не обработались (processing_status=Failure), причины
пишутся рядом с таблицей в `<результат>.errors.csv` (ТЗ п.2.7: все ошибки
фиксируются в отчёте; в саму таблицу сдачи лишние колонки не добавляем).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from ai_module.batch import COLUMNS, ArchiveLimitError, process_file, run_batch, write_sr_files, write_table  # noqa: F401 — process_file реэкспорт

__all__ = ["COLUMNS", "process_file", "run_batch", "main"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Пакетный прогон DICOM -> таблица результатов (ТЗ п.2.5)")
    parser.add_argument("input", help="Папка с DICOM (рекурсивно), zip-архив или один DICOM-файл")
    parser.add_argument("output", help="Путь к результату: .xlsx или .csv")
    parser.add_argument("--engine", choices=["hybrid", "heuristics"], default="hybrid",
                        help="Движок анализа (по умолчанию hybrid — CNN для укладки бедра; heuristics — без torch)")
    parser.add_argument("--sr", metavar="ПАПКА", help="Сохранить отчёты DICOM SR по каждому снимку в эту папку")
    args = parser.parse_args()

    try:
        rows = run_batch(args.input, args.engine, with_sr=bool(args.sr))
    except (FileNotFoundError, ArchiveLimitError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return 2
    write_table(rows, args.output)

    failures = [r for r in rows if r["processing_status"] != "Success"]
    print(f"обработано файлов: {len(rows)}, Success: {len(rows) - len(failures)}, Failure: {len(failures)}")
    print(f"результат: {args.output}")
    if args.sr:
        print(f"отчёты DICOM SR: {write_sr_files(rows, args.sr)} шт. в {args.sr}")
    if not rows:
        print("предупреждение: DICOM-файлы не найдены", file=sys.stderr)
    if failures:
        err_path = Path(args.output).with_suffix(".errors.csv")
        with open(err_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["path_to_study", "error"])
            writer.writerows([r["path_to_study"], r["error"]] for r in failures)
        print(f"причины ошибок: {err_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
