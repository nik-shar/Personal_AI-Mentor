"""
scripts/tests/test_messenger.py

Verification test suite for Milestone 3:
Third-Party Proactive Messenger Integration.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from integrations.messenger import send_proactive_notification


def test_messenger():
    print("=== Testing Proactive Messenger Integration ===")
    test_msg = "Test Message."
    success = send_proactive_notification(test_msg, title="Test Telegram Bot")
    assert success is True
    print("✅ Messenger integration test completed cleanly!")


if __name__ == "__main__":
    test_messenger()
    print("\n🎉 MESSENGER INTEGRATION TESTS PASSED SUCCESSFULLY!")
