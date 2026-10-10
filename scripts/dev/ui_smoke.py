"""Browser smoke test: drive the real React dashboard in Edge and screenshot every page.

Fails loudly on any console error, uncaught exception or CSP violation, which are exactly the
failures a headless HTTP test cannot see. Run the demo backend first (it seeds the `owner`
account in a throwaway SQLite database):

    powershell -File scripts/dev/run_demo_server.ps1
    python scripts/dev/ui_smoke.py [base_url] [out_dir]

Needs:  pip install playwright   (uses the installed Microsoft Edge; no browser download)
"""
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8077"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)
USER, PASSWORD = "owner", "correct horse battery staple"
problems: list[str] = []


def shot(page: Page, name: str, full: bool = False) -> None:
    page.wait_for_timeout(500)  # let entry animations settle
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)


def sign_in(page: Page) -> None:
    page.fill("input[autocomplete=username]", USER)
    page.fill("input[autocomplete=current-password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector(".shell")


def run_desktop(browser) -> None:
    page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
    unauthorized: list[str] = []
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: unauthorized.append(f"{r.request.method} {r.url.replace(BASE, '')}") if r.status == 401 else None)

    page.goto(BASE + "/")
    page.wait_for_selector("form.login-card")
    shot(page, "01-login")

    # wrong password first: the UI must show a generic error, not crash
    page.fill("input[autocomplete=username]", USER)
    page.fill("input[autocomplete=current-password]", "definitely-wrong-password")
    page.click("button[type=submit]")
    page.wait_for_selector(".form-error:not(:empty)")
    print("login error shown:", page.inner_text(".form-error"))

    sign_in(page)
    page.wait_for_timeout(1200)
    shot(page, "02-overview")

    # talk to JARVIS from the overview
    page.fill("#command-input", "remember that my car is blue")
    page.keyboard.press("Enter")
    page.wait_for_selector(".bubble.agent")
    shot(page, "03-overview-after-talk")

    for name in ("Agents", "Memory", "Approvals", "System"):
        page.click(f".nav button:has-text('{name}')")
        page.wait_for_timeout(1200)
        shot(page, f"04-{name.lower()}", full=True)
        print("visited", name)

    # agents page: open SHURI, voice tab, talk tab
    page.click(".nav button:has-text('Agents')")
    page.wait_for_selector(".agent-card")
    page.click(".agent-card:has-text('SHURI')")
    page.click("button[role=tab]:has-text('Voice')")
    shot(page, "05-agent-voice", full=True)
    page.click("button[role=tab]:has-text('Talk')")
    page.fill("input[id^=task-]", "research agent security")
    page.keyboard.press("Enter")
    page.wait_for_selector(".reply")
    shot(page, "06-agent-talk", full=True)

    # light theme
    page.click("button[aria-label='Switch to light theme']")
    page.click(".nav button:has-text('Overview')")
    shot(page, "07-light-overview")

    # session survives a reload via the HttpOnly refresh cookie (no token in storage)
    page.reload()
    page.wait_for_selector(".shell", timeout=8000)
    print("session restored after reload: yes")
    stored = page.evaluate("JSON.stringify([localStorage, sessionStorage])")
    print("tokens in web storage:", "eyJ" in stored)
    if "eyJ" in stored:
        problems.append("a JWT was found in web storage")
    cookies = {c["name"]: c for c in page.context.cookies()}
    refresh = cookies.get("jarvis_refresh", {})
    print("refresh cookie httpOnly/sameSite:", refresh.get("httpOnly"), refresh.get("sameSite"))
    if not refresh.get("httpOnly"):
        problems.append("refresh cookie is not HttpOnly")

    # sign out really ends the session
    page.click("button[aria-label='Sign out']")
    page.wait_for_selector("form.login-card")
    print("signed out -> login screen: yes")
    page.close()

    # The browser logs every non-2xx fetch as a console error. These 401s are EXPECTED: the
    # first-load session probe (no cookie yet) and the deliberate wrong-password login.
    expected = {"POST /api/auth/refresh", "POST /api/auth/login"}
    print("401 responses seen:", unauthorized)
    problems.extend(f"unexpected 401: {u}" for u in unauthorized if u not in expected)
    problems[:] = [p for p in problems if "status of 401" not in p]


def run_mobile(browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True, color_scheme="dark")
    page = context.new_page()
    page.on("pageerror", lambda e: problems.append(f"mobile pageerror: {e}"))
    page.goto(BASE + "/")
    page.wait_for_selector("form.login-card")
    shot(page, "10-mobile-login")
    sign_in(page)
    page.wait_for_timeout(1000)
    shot(page, "11-mobile-overview")
    page.click(".tabbar button:has-text('Agents')")
    page.wait_for_selector(".agent-card")
    shot(page, "12-mobile-agents")
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    if overflow:
        problems.append("mobile layout scrolls horizontally")
    context.close()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        run_desktop(browser)
        run_mobile(browser)
        browser.close()

    print("\nPROBLEMS:" if problems else "\nNo console errors, page errors, CSP violations, or unexpected 401s.")
    for line in problems:
        print("  -", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
