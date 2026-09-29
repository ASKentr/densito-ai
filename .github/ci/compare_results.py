"""Сверка результата пакетной обработки в Docker (Linux) с эталоном, полученным
на Windows без Docker (.github/ci/expected_sample_results.csv).

    python3 compare_results.py <results.csv> <папка_sr>

Сравниваются все колонки, кроме пути и времени; quality_prob — с допуском 0,01
(PyTorch на разных CPU может отличаться в последних знаках). Проверяется и число
отчётов DICOM SR (по одному на каждый успешно обработанный снимок).
"""
import csv
import sys
from pathlib import Path, PurePosixPath

HERE = Path(__file__).parent
PROB_TOL = 0.01


def main() -> int:
    got_path, sr_dir = Path(sys.argv[1]), Path(sys.argv[2])
    expected = {r["file"]: r for r in csv.DictReader(open(HERE / "expected_sample_results.csv", encoding="utf-8"))}
    got = {PurePosixPath(r["path_to_study"].replace("\\", "/")).name: r
           for r in csv.DictReader(open(got_path, encoding="utf-8-sig"))}

    errors = []
    if set(got) != set(expected):
        errors.append(f"набор файлов отличается: лишние {sorted(set(got) - set(expected))}, "
                      f"нет {sorted(set(expected) - set(got))}")
    for name in sorted(set(got) & set(expected)):
        e, g = expected[name], got[name]
        for col in ("anatomical_region", "quality_class", "violation_type", "processing_status"):
            if (g[col] or "") != (e[col] or ""):
                errors.append(f"{name}: {col} = {g[col]!r}, ожидалось {e[col]!r}")
        if e["quality_prob"] and abs(float(g["quality_prob"] or "nan") - float(e["quality_prob"])) > PROB_TOL:
            errors.append(f"{name}: quality_prob = {g['quality_prob']}, ожидалось {e['quality_prob']} ± {PROB_TOL}")
        try:
            if float(g["time_of_processing"]) > 180:
                errors.append(f"{name}: обработка дольше 3 минут (ТЗ п.2.7)")
        except ValueError:
            errors.append(f"{name}: нет time_of_processing")

    n_success = sum(1 for e in expected.values() if e["processing_status"] == "Success")
    n_sr = len(list(sr_dir.rglob("*.sr.dcm")))
    if n_sr != n_success:
        errors.append(f"отчётов DICOM SR {n_sr}, ожидалось {n_success}")

    if errors:
        print("РАСХОЖДЕНИЯ С ЭТАЛОНОМ:")
        print("\n".join("  - " + x for x in errors))
        return 1
    print(f"OK: {len(got)} файлов совпали с эталоном Windows, отчётов DICOM SR: {n_sr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
