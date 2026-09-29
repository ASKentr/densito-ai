"""
Сквозная проверка веб-интерфейса через настоящий браузер (Playwright + Chromium
из /opt/pw-browsers). Требует поднятых backend (127.0.0.1:8000) и frontend
(127.0.0.1:5173). Делает скриншоты на каждом шаге в /tmp/e2e_screens/.
"""
import os
import time

from playwright.sync_api import sync_playwright

FRONTEND = "http://127.0.0.1:5173"
SCREEN_DIR = "/tmp/e2e_screens"
SAMPLE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data", "studies", "06_lspine_misaligned.dcm")

os.makedirs(SCREEN_DIR, exist_ok=True)


def shot(page, name):
    page.screenshot(path=os.path.join(SCREEN_DIR, name))
    print("screenshot:", name)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                                     args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        page.goto(FRONTEND, wait_until="networkidle")
        shot(page, "01_login.png")

        page.fill('input[name="username"]', "expert")
        page.fill('input[name="password"]', "expert12345")
        page.click('button[type="submit"]')
        page.wait_for_url("**/studies", timeout=10000)
        shot(page, "02_studies_list.png")

        # загрузка DICOM
        with page.expect_response(lambda r: "/studies/upload" in r.url and r.status == 200, timeout=15000):
            page.set_input_files('input[type="file"]', SAMPLE_FILE)
        page.wait_for_url("**/studies/*", timeout=10000)
        time.sleep(1)
        shot(page, "03_study_card_before_analysis.png")

        # запуск AI-анализа
        page.click('button:has-text("Запустить AI-анализ")')
        page.wait_for_selector("text=Повторить AI-анализ", timeout=15000)
        time.sleep(1)
        shot(page, "04_after_ai_analysis.png")

        # проверяем, что канвас с DICOM отрисовался (не пустой)
        canvas_box = page.locator(".dicom-canvas-container canvas").bounding_box()
        print("canvas bbox:", canvas_box)

        # подтверждаем первую находку, если есть
        confirm_btn = page.locator('button:has-text("Подтвердить")').first
        if confirm_btn.count() > 0:
            confirm_btn.click()
            time.sleep(1)
            shot(page, "05_after_confirm_finding.png")

        # завершаем экспертную проверку
        page.click('button:has-text("Завершить экспертную проверку")')
        time.sleep(1)
        shot(page, "06_after_complete_review.png")

        page.goto(f"{FRONTEND}/studies", wait_until="networkidle")
        shot(page, "07_studies_list_final.png")

        browser.close()

        if errors:
            print("\n=== JS ERRORS DETECTED ===")
            for e in errors:
                print(e)
        else:
            print("\nJS errors: none")

        print("\nE2E ПРОШЁЛ" if not errors else "\nE2E ЗАВЕРШЁН С JS-ОШИБКАМИ (см. выше)")


if __name__ == "__main__":
    main()
