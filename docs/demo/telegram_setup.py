"""One-shot Telegram wiring for a local demo.

    python docs/demo/telegram_setup.py

Checks the token, waits for you to message the bot, reads the chat id off that
message, and writes both into `.env` with polling switched on. Then restart the
API and held actions appear on your phone.

Why polling and not a webhook: a webhook needs a public HTTPS URL for Telegram
to call back to, and a laptop does not have one. Polling asks Telegram for
updates instead, so nothing has to be reachable from the internet. The decision
path either way is identical — same signature check, same audit entry.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
API = "https://api.telegram.org"


def api(token: str, method: str) -> str:
    return f"{API}/bot{quote(token, safe='')}/{method}"


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    """Set key=value, replacing any existing definition, commented or not."""
    out = [ln for ln in lines if not ln.strip().lstrip("#").strip().startswith(f"{key}=")]
    out.append(f"{key}={value}")
    return out


def main() -> int:
    token = (sys.argv[1] if len(sys.argv) > 1
             else os.environ.get("MEMBRANE_TELEGRAM_BOT_TOKEN", "")).strip()
    if not token:
        print("usage: python docs/demo/telegram_setup.py <bot-token>")
        print("  get one from @BotFather -> /newbot")
        return 2

    with httpx.Client(timeout=40.0) as client:
        me = client.get(api(token, "getMe")).json()
        if not me.get("ok"):
            print(f"  token rejected: {me.get('description')}")
            return 1
        bot = me["result"]["username"]
        print(f"  bot          @{bot}")

        # A registered webhook and polling are mutually exclusive; Telegram
        # answers getUpdates with 409 while one is set.
        info = client.get(api(token, "getWebhookInfo")).json()
        if (info.get("result") or {}).get("url"):
            client.post(api(token, "deleteWebhook"))
            print("  webhook      removed (polling needs the update stream)")

        print(f"\n  Open Telegram, find @{bot}, and press Start.")
        print("  Waiting", end="", flush=True)

        chat = None
        deadline = time.time() + 180
        while time.time() < deadline and chat is None:
            updates = client.get(api(token, "getUpdates")).json().get("result") or []
            for update in updates:
                for holder in ("message", "my_chat_member", "callback_query"):
                    node = update.get(holder) or {}
                    found = node.get("chat") or (node.get("message") or {}).get("chat")
                    if found:
                        chat = found
                        break
                if chat:
                    break
            if chat is None:
                print(".", end="", flush=True)
                time.sleep(3)

        if chat is None:
            print("\n  no message arrived within three minutes.")
            return 1

        who = chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
        print(f"\n  chat id      {chat['id']}  ({chat.get('type')}, {who})")

    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = upsert(lines, "MEMBRANE_TELEGRAM_BOT_TOKEN", token)
    lines = upsert(lines, "MEMBRANE_TELEGRAM_CHAT_ID", str(chat["id"]))
    lines = upsert(lines, "MEMBRANE_TELEGRAM_POLLING", "true")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n  wrote        {ENV}  (gitignored)")
    print("\n  Now restart the API so it picks the settings up:")
    print("    docker compose up -d api")
    print("\n  Then check it took:")
    print("    curl -s http://localhost:8080/readyz")
    print("    -> telegram_configured: true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
