"""Самопроверка ИИ-модуля (ai_module/status.py) и GET /health."""
from pathlib import Path

from fastapi.testclient import TestClient

from ai_module import hybrid, learned_scorers
from ai_module.status import engine_status


def test_engine_ready():
    st = engine_status()
    assert st["ready"] is True
    assert st["model_version"] == hybrid.MODEL_VERSION
    assert [c["name"] for c in st["components"]] == ["Правила ТЗ", "Модели позвоночника", "Нейросеть укладки бедра"]
    assert all(c["ok"] for c in st["components"])


def test_missing_cnn_weights_is_reported(monkeypatch):
    monkeypatch.setattr(hybrid, "WEIGHTS_PATH", Path("нет_такого_файла.pt"))
    monkeypatch.setattr(hybrid, "_model", None)
    st = engine_status()
    assert st["ready"] is False
    cnn = st["components"][2]
    assert not cnn["ok"] and "нет_такого_файла.pt" in cnn["detail"]


def test_missing_spine_scorers_is_reported(monkeypatch):
    monkeypatch.setattr(learned_scorers, "WEIGHTS_PATH", Path("нет_такого_файла.json"))
    monkeypatch.setattr(learned_scorers, "_cache", None)
    st = engine_status()
    assert st["ready"] is False and not st["components"][1]["ok"]


def test_health_endpoint():
    from app.main import app
    r = TestClient(app).get("/health")          # без авторизации
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["ai"]["ready"] is True
