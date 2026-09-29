import os
from playwright.sync_api import sync_playwright

FRONTEND = "http://127.0.0.1:5173"
SCREEN_DIR = "/tmp/e2e_screens"
os.makedirs(SCREEN_DIR, exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                                     args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        page.goto(FRONTEND, wait_until="networkidle")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin12345")
        page.click('button[type="submit"]')
        page.wait_for_url("**/studies", timeout=10000)

        page.click('text=Справочник нарушений')
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SCREEN_DIR, "10_admin_violations.png"))

        page.click('text=Пользователи')
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SCREEN_DIR, "11_admin_users.png"))

        page.click('text=Статистика')
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SCREEN_DIR, "12_admin_stats.png"))

        browser.close()
        print("JS errors:", errors if errors else "none")


if __name__ == "__main__":
    main()
