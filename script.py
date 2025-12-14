import os
import json
import re
import requests
import hashlib
import difflib
from datetime import datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

load_dotenv()

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "last_snapshot.json"
SNAPSHOT_DIR = STATE_DIR / "snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)

def send_pushover(message: str, title: str = "Watcher"):
    token = (os.getenv("PUSHOVER_APP_TOKEN") or "").strip()
    user  = (os.getenv("PUSHOVER_USER_KEY") or "").strip()

    payload = {
        "token": token,     # required :contentReference[oaicite:1]{index=1}
        "user": user,       # required :contentReference[oaicite:2]{index=2}
        "message": message, # required :contentReference[oaicite:3]{index=3}
        "title": title,
    }

    r = requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=15)

    # If it fails, print the exact Pushover error JSON (it includes `errors`)
    if r.status_code != 200:
        raise RuntimeError(f"Pushover {r.status_code}: {r.text}")  # :contentReference[oaicite:4]{index=4}

    return r.json()

def normalize_text(s: str) -> str:
    # Remove excessive whitespace and common “noise” patterns if needed
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def load_last():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))

def save_state(data: dict):
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def write_diff(old_text: str, new_text: str) -> str:
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm=""
    )
    return "\n".join(diff)

def main():
    now = datetime.now(ZoneInfo("America/Chicago")).time()

# Quiet hours: midnight -> 7:00am
    # if time(0, 0) <= now < time(7, 0):
    #     print("Skipping due to quiet hours (12:00 AM–7:00 AM CT).")
    #     return
    
    login_url = os.environ["LOGIN_URL"]
    target_url = os.environ["TARGET_URL"]
    username = os.environ["USERNAME"]
    password = os.environ["PASSWORD"]

    user_sel = os.environ["USERNAME_SELECTOR"]
    pass_sel = os.environ["PASSWORD_SELECTOR"]
    submit_sel = os.environ["SUBMIT_SELECTOR"]
    content_sel = os.environ.get("CONTENT_SELECTOR", "body")

    last = load_last()
    last_hash = last["hash"] if last else None
    last_text = last["text"] if last else ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Go to login page
        page.goto(login_url, wait_until="domcontentloaded")

        # Fill and submit login form
        page.fill(user_sel, username)
        page.fill(pass_sel, password)
        page.click(submit_sel)

        # Wait for navigation / authenticated state
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeoutError:
            # Some sites never go "idle" — not fatal.
            pass

        # Go to the target page you want to monitor
        page.goto(target_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeoutError:
            pass

        # Extract monitored content
        content = page.locator(content_sel).inner_text()
        browser.close()

    new_text = normalize_text(content)
    new_hash = sha256(new_text)
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if last_hash is None:
        print("No previous snapshot found. Saving initial snapshot.")
        save_state({"hash": new_hash, "text": new_text, "timestamp": now})
        (SNAPSHOT_DIR / f"{now}.txt").write_text(new_text, encoding="utf-8")
        send_pushover(f"CHANGE DETECTED: {new_text}")
        return

    if new_hash == last_hash:
        print("No change detected.")
        send_pushover(f"CHANGE DETECTED: {new_text}")
        return

    print("CHANGE DETECTED ✅")

    diff_text = write_diff(last_text, new_text)
    (SNAPSHOT_DIR / f"{now}.txt").write_text(new_text, encoding="utf-8")
    (SNAPSHOT_DIR / f"{now}.diff.txt").write_text(diff_text, encoding="utf-8")

    save_state({"hash": new_hash, "text": new_text, "timestamp": now})

    print(f"Saved snapshot: {SNAPSHOT_DIR / f'{now}.txt'}")
    print(f"Saved diff:     {SNAPSHOT_DIR / f'{now}.diff.txt'}")
    send_pushover(f"CHANGE DETECTED: {new_text}")
if __name__ == "__main__":
    main()

