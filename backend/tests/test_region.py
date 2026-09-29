"""Определение анатомической области: симметрия + модель по миниатюре
(heuristics.classify_anatomical_region, region_model.py)."""
import numpy as np

from ai_module import heuristics, region_model

REAL_SIDE = 256     # сторона снимка аппарата (домен обучения модели 150–400 px)


def _symmetric_mask(side: int) -> np.ndarray:
    mask = np.zeros((side, side), bool)
    mask[:, side // 2 - side // 8: side // 2 + side // 8] = True   # вертикальный столб по центру
    return mask


def _asymmetric_mask(side: int) -> np.ndarray:
    """«Г»-образный объект (как диафиз с шейкой бедра): несимметричен ни
    относительно центра кадра, ни относительно собственного центроида."""
    mask = np.zeros((side, side), bool)
    mask[side // 6: side - 6, side // 8: side // 4] = True
    mask[side // 6: side // 3, side // 8: 3 * side // 4] = True
    return mask


def _model_says(monkeypatch, p_spine: float) -> list:
    calls = []

    def fake(img8):
        calls.append(img8.shape)
        return p_spine
    monkeypatch.setattr(region_model, "available", lambda: True)
    monkeypatch.setattr(region_model, "spine_probability", fake)
    return calls


def test_weights_load_and_give_probability():
    assert region_model.available()
    img8 = np.random.default_rng(0).integers(0, 255, (REAL_SIDE, 200), dtype=np.uint8)
    assert region_model.region_features(img8).shape == (region_model.THUMB ** 2,)
    assert 0.0 <= region_model.spine_probability(img8) <= 1.0


def test_symmetric_hip_is_decided_by_model(monkeypatch):
    """Сценарий найденной ошибки: снимок бедра с высокой симметрией (таз целиком,
    эндопротез) раньше уходил в «позвоночник» — теперь решает модель."""
    calls = _model_says(monkeypatch, 0.1)
    img8 = np.zeros((REAL_SIDE, REAL_SIDE), np.uint8)
    region, confidence, symmetry = heuristics.classify_anatomical_region(_symmetric_mask(REAL_SIDE), img8)
    assert symmetry >= heuristics.REGION_SYMMETRY_THRESHOLD
    assert region == "hip" and confidence == 0.9 and calls


def test_symmetric_spine_stays_spine(monkeypatch):
    _model_says(monkeypatch, 0.97)
    img8 = np.zeros((REAL_SIDE, REAL_SIDE), np.uint8)
    region, _, _ = heuristics.classify_anatomical_region(_symmetric_mask(REAL_SIDE), img8)
    assert region == "spine"


def test_low_symmetry_is_hip_without_asking_model(monkeypatch):
    calls = _model_says(monkeypatch, 0.99)
    img8 = np.zeros((REAL_SIDE, REAL_SIDE), np.uint8)
    region, _, symmetry = heuristics.classify_anatomical_region(_asymmetric_mask(REAL_SIDE), img8)
    assert symmetry < heuristics.REGION_SYMMETRY_THRESHOLD
    assert region == "hip" and not calls


def test_out_of_domain_image_uses_symmetry_rule(monkeypatch):
    """Синтетика sample_data (768 px) вне домена модели — прежнее правило."""
    calls = _model_says(monkeypatch, 0.0)
    img8 = np.zeros((768, 768), np.uint8)
    region, _, _ = heuristics.classify_anatomical_region(_symmetric_mask(768), img8)
    assert region == "spine" and not calls


def test_without_image_uses_symmetry_rule(monkeypatch):
    calls = _model_says(monkeypatch, 0.0)
    region, _, _ = heuristics.classify_anatomical_region(_symmetric_mask(REAL_SIDE))
    assert region == "spine" and not calls
