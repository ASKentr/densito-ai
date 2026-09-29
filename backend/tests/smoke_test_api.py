"""Сквозной прогон сценария через реальный HTTP API (не через pytest — руками,
для наглядной проверки перед сдачей). Запускать при поднятом backend
(uvicorn app.main:app) и сгенерированных sample_data.
"""
import os
import sys

import requests

BASE = "http://127.0.0.1:8000"
SAMPLES = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "studies")


def main():
    r = requests.post(f"{BASE}/auth/login", data={"username": "expert", "password": "expert12345"})
    r.raise_for_status()
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("1. Логин эксперта: OK")

    with open(os.path.join(SAMPLES, "06_lspine_misaligned.dcm"), "rb") as f:
        r = requests.post(f"{BASE}/studies/upload", headers=headers,
                           files={"file": ("06_lspine_misaligned.dcm", f, "application/dicom")})
    r.raise_for_status()
    study = r.json()
    study_id = study["id"]
    print(f"2. Загрузка DICOM: OK, study_id={study_id}, display_id={study['display_id']}, "
          f"anonymization_ok={study['anonymization_ok']}")
    assert study["status"] == "uploaded"

    r = requests.get(f"{BASE}/studies", headers=headers)
    r.raise_for_status()
    assert any(s["id"] == study_id for s in r.json())
    print(f"3. Список исследований: OK, {len(r.json())} записей")

    r = requests.post(f"{BASE}/studies/{study_id}/analyze", headers=headers)
    r.raise_for_status()
    analyzed = r.json()
    print(f"4. AI-анализ: OK, verdict={analyzed['overall_verdict']}, score={analyzed['overall_score']}, "
          f"findings={len(analyzed['findings'])}")
    assert analyzed["status"] == "analyzed"
    assert len(analyzed["findings"]) > 0
    finding = analyzed["findings"][0]

    r = requests.get(f"{BASE}/studies/{study_id}/images/{analyzed['images'][0]['id']}/preview", headers=headers)
    r.raise_for_status()
    assert r.headers["content-type"] == "image/png"
    print(f"5. Превью снимка: OK, {len(r.content)} байт PNG")

    r = requests.patch(f"{BASE}/findings/{finding['id']}", headers=headers,
                        json={"action": "confirm", "comment": "Подтверждаю, смещение видно явно"})
    r.raise_for_status()
    print(f"6. Экспертное подтверждение находки: OK, status={r.json()['status']}")

    # справочник нарушений (нужен id для добавления своей находки)
    r = requests.post(f"{BASE}/auth/login", data={"username": "admin", "password": "admin12345"})
    admin_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = requests.get(f"{BASE}/admin/violation-types", headers=admin_headers)
    r.raise_for_status()
    vt_id = next(v["id"] for v in r.json() if v["code"] == "artifact")

    r = requests.post(f"{BASE}/studies/{study_id}/findings", headers=headers, json={
        "image_id": analyzed["images"][0]["id"], "violation_type_id": vt_id,
        "severity": "low", "bbox_x": 0.1, "bbox_y": 0.1, "bbox_w": 0.1, "bbox_h": 0.1,
        "comment": "Добавлено экспертом вручную для проверки сценария",
    })
    r.raise_for_status()
    print(f"7. Добавление экспертной находки: OK, id={r.json()['id']}")

    r = requests.post(f"{BASE}/studies/{study_id}/complete_review", headers=headers)
    r.raise_for_status()
    print("8. Завершение экспертной проверки: OK")

    r = requests.get(f"{BASE}/studies/{study_id}", headers=headers)
    r.raise_for_status()
    final = r.json()
    assert final["review_status"] == "reviewed"
    print(f"9. Финальная проверка карточки: review_status={final['review_status']}, "
          f"findings_count={final['findings_count']}")

    r = requests.get(f"{BASE}/admin/stats", headers=admin_headers)
    r.raise_for_status()
    print(f"10. Статистика админа: {r.json()}")

    print("\nПОЛНЫЙ СЦЕНАРИЙ ПРОЙДЕН УСПЕШНО")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)
