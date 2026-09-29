"""
Проверяет, что эвристический AI-движок (ai_module/heuristics.py) правильно
классифицирует синтетические тестовые исследования из sample_data/studies/
(сгенерированы sample_data/generate_synthetic_studies.py, manifest.json
содержит ожидаемый вердикт/категорию для каждого файла).

Запуск: из backend/  ->  python -m pytest tests/ -v
"""
import json
import os

import pytest

from ai_module.heuristics import analyze_study

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "studies")
MANIFEST_PATH = os.path.join(SAMPLE_DIR, "manifest.json")


def _load_manifest():
    if not os.path.exists(MANIFEST_PATH):
        pytest.skip("Синтетические сэмплы не сгенерированы: запустите "
                     "sample_data/generate_synthetic_studies.py")
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("item", _load_manifest() if os.path.exists(MANIFEST_PATH) else [])
def test_synthetic_scenario_detected(item):
    path = os.path.join(SAMPLE_DIR, item["file"])
    result = analyze_study([path])
    codes = {f.violation_code for f in result.findings}

    if item["expected_category"] is None:
        assert result.overall_verdict == "qualitative", (
            f"{item['file']}: ожидали качественное исследование, получили "
            f"{result.overall_verdict}, codes={codes}"
        )
    else:
        assert item["expected_category"] in codes, (
            f"{item['file']}: ожидали найти '{item['expected_category']}', "
            f"нашли {codes}"
        )
        assert result.overall_verdict == "non_qualitative"


def test_analyze_study_handles_unreadable_file(tmp_path):
    bad_file = tmp_path / "not_a_dicom.dcm"
    bad_file.write_bytes(b"this is not a dicom file")
    result = analyze_study([str(bad_file)])
    assert result.overall_verdict == "non_qualitative"
    assert any(f.violation_code == "dicom_technical_error" for f in result.findings)
