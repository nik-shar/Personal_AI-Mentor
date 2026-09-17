"""
integrations/messenger.py

Third-Party Messaging & Phone Notification Layer (Milestone 3).

Supports pushing proactive mentor check-ins, reminders, and daily review prompts
directly to your phone or external chat platforms via:
1. Slack Webhooks (SLACK_WEBHOOK_URL)
2. Telegram Bot API (TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID)
3. Custom HTTP Webhooks (PROACTIVE_WEBHOOK_URL)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("messenger_integration")


def send_slack_notification(message: str, webhook_url: Optional[str] = None) -> bool:
    """Send message to a Slack channel via Incoming Webhook."""
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return False
    try:
        payload = {"text": message}
        resp = requests.post(url, json=payload, timeout=5.0)
        return resp.status_code == 200
    except Exception as exc:
        print(f"[Messenger] Slack notification failed: {exc}")
        return False


def send_telegram_notification(
    message: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send message to Telegram chat via Telegram Bot API."""
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip().strip("'").strip('"')
    cid = (chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip().strip("'").strip('"')
    if not token or not cid:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=5.0)
        if resp.status_code == 200:
            return True
        
        # Fallback without Markdown if markdown formatting failed
        payload_plain = {"chat_id": cid, "text": message}
        resp_plain = requests.post(url, json=payload_plain, timeout=5.0)
        if resp_plain.status_code == 200:
            return True
        print(f"[Messenger] Telegram notification error: {resp_plain.status_code} - {resp_plain.text}")
        return False
    except Exception as exc:
        print(f"[Messenger] Telegram notification exception: {exc}")
        return False


def send_proactive_notification(
    message: str,
    title: Optional[str] = None,
    channel: Optional[str] = None,
) -> bool:
    """
    Unified entry point for third-party proactive messaging.
    Tries Slack -> Telegram -> Custom Webhook -> Local Log fallback.
    """
    load_dotenv(override=True)
    formatted_msg = f"🧠 *{title or 'AI Mentor Proactive Check-In'}*\n\n{message}"

    # Try Slack
    if send_slack_notification(formatted_msg):
        print("📱 [Messenger] Proactive notification delivered via Slack.")
        return True

    # Try Telegram
    if send_telegram_notification(formatted_msg):
        print("📱 [Messenger] Proactive notification delivered via Telegram.")
        return True

    # Try Custom Webhook
    custom_url = os.getenv("PROACTIVE_WEBHOOK_URL")
    if custom_url:
        try:
            resp = requests.post(custom_url, json={"title": title, "message": message}, timeout=5.0)
            if resp.status_code == 200:
                print("📱 [Messenger] Proactive notification delivered via Custom Webhook.")
                return True
        except Exception:
            pass

    # Fallback log
    print(f"📱 [Messenger] Proactive notification logged locally:\n{formatted_msg}")
    print("   (Configure SLACK_WEBHOOK_URL or TELEGRAM_BOT_TOKEN in .env to receive notifications on your phone)")
    return True
