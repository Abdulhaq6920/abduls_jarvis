"""Browser smoke test: drive the real dashboard in Edge and screenshot every page.

Fails loudly on any console error, uncaught exception, or CSP violation, which are exactly
the failures a headless HTTP test cannot see.

    python scripts/ui_smoke.py [base_url] [out_dir]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8077"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)
problems: list[str] = []


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1360, "height": 860})
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        unauthorized: list[str] = []
        page.on("response", lambda r: unauthorized.append(f"{r.request.method} {r.url.replace(BASE, '')}") if r.status == 401 else None)

        page.goto(BASE + "/")
        page.wait_for_selector("form.login")
        page.screenshot(path=str(OUT / "01-login.png"))

        # wrong password first: the UI must show a generic error, not crash
        page.fill("input[aria-label=Username]", "owner")
        page.fill("input[aria-label=Password]", "definitely-wrong-password")
        page.click("button[type=submit]")
        page.wait_for_selector(".error:not(:empty)")
        print("login error shown:", page.inner_text(".error"))

        page.fill("input[aria-label=Username]", "owner")
        page.fill("input[aria-label=Password]", "correct horse battery staple")
        page.click("button[type=submit]")
        page.wait_for_selector(".shell")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "02-overview.png"))

        # talk to JARVIS from the overview
        page.fill("input[aria-label='Type a request']", "remember that my car is blue")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "03-overview-after-talk.png"))

        for name in ("agents", "memory", "approvals", "system"):
            page.click(f".nav button:has-text('{name.capitalize()}')")
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / f"04-{name}.png"), full_page=True)
            print("visited", name)

        # agents page: open SHURI, voice tab, talk tab
        page.click(".nav button:has-text('Agents')")
        page.wait_for_selector(".agent")
        page.click(".agent:has-text('SHURI')")
        page.click("button[role=tab]:has-text('Voice')")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "05-agent-voice.png"), full_page=True)
        page.click("button[role=tab]:has-text('Talk to agent')")
        page.fill("input[aria-label=Task]", "research agent security")
        page.keyboard.press("Enter")
        page.wait_for_selector(".reply")
        page.screenshot(path=str(OUT / "06-agent-talk.png"), full_page=True)

        # session survives a reload via the HttpOnly refresh cookie (no token in storage)
        page.reload()
        page.wait_for_selector(".shell", timeout=8000)
        print("session restored after reload: yes")
        stored = page.evaluate("JSON.stringify([localStorage, sessionStorage])")
        print("tokens in web storage:", "eyJ" in stored)
        cookies = {c["name"]: c for c in page.context.cookies()}
        rc = cookies.get("jarvis_refresh", {})
        print("refresh cookie httpOnly/sameSite:", rc.get("httpOnly"), rc.get("sameSite"))

        # sign out really ends the session
        page.click("text=Sign out")
        page.wait_for_selector("form.login")
        print("signed out -> login screen: yes")
        browser.close()

    # The browser logs every non-2xx fetch as a console error. These two 401s are EXPECTED:
    # the first-load session probe (no cookie yet) and the deliberate wrong-password login.
    expected = {"POST /api/auth/refresh", "POST /api/auth/login"}
    print("401 responses seen:", unauthorized)
    unexpected = [u for u in unauthorized if u not in expected]
    problems[:] = [p for p in problems if "status of 401" not in p]
    problems.extend(f"unexpected 401: {u}" for u in unexpected)

    print("\nPROBLEMS:" if problems else "\nNo console errors, page errors, CSP violations, or unexpected 401s.")
    for line in problems:
        print("  -", line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
