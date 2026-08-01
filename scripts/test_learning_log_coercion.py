"""
scripts/test_learning_log_coercion.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agents.Daily_Coach.state import _coerce_learning_log
from schemas.memory import LearningLogEntry

def test_coercion():
    print("=== Testing LearningLogEntry Safe Coercion ===")

    malformed_entries = [
        {"topics": ["Agentic AI"], "source": "chat"},
        {"id": "041ac204-fdd5-44a", "embedding": None}, # Missing date & topics
        {"date": "2026-07-30T00:00:00Z", "topics": ["LangGraph Checkpoints"], "confirmed_by_user": True},
    ]

    coerced = _coerce_learning_log(malformed_entries)
    print(f"✅ Coerced {len(coerced)} items from malformed dicts:")
    for entry in coerced:
        print(f"  - Entry: date={entry.date}, topics={entry.topics}")

    assert len(coerced) == 3, f"Expected 3 valid coerced entries, got {len(coerced)}"
    print("\n🎉 SAFE COERCION TEST PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_coercion()
