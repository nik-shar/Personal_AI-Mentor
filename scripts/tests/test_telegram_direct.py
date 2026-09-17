"""
scripts/tests/test_telegram_direct.py
Diagnostic test for Telegram Bot API notification.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

print(f"Token present: {bool(token)} (length: {len(token) if token else 0})")
print(f"Chat ID present: {bool(chat_id)} (value: {chat_id})")

if token and chat_id:
    # Strip accidental whitespace or quotes
    token = token.strip().strip("'").strip('"')
    chat_id = chat_id.strip().strip("'").strip('"')
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "🧠 *Personal AI Mentor Live Test*\n\nHello Nikhil! Your AI Mentor proactive notification channel is active and verified. 🚀",
        "parse_mode": "Markdown",
    }
    print("Posting to: https://api.telegram.org/bot<TOKEN>/sendMessage ...")
    try:
        resp = requests.post(url, json=payload, timeout=8.0)
        print(f"HTTP Status Code: {resp.status_code}")
        print(f"Response Body: {resp.text}")
    except Exception as exc:
        print(f"Request Exception: {exc}")
