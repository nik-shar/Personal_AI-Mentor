"""
scripts/tools/reset_memory.py

Wipes old stored memories, profile facts, and episodic logs, leaving
a completely fresh slate for pure conversational discovery.
"""

import sys
from pathlib import Path

# Ensure project root is importable when running script directly
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from orchestrator.config import DB_URL


def reset_memory():
    print("Connecting to database to reset memory...")

    # Make sure all tables (incl. newer ones like conversation_sessions)
    # exist before truncating.
    from orchestrator.memory.store import MemoryManager
    MemoryManager().ensure_schema()

    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        print("Clearing persistent memory tables...")
        conn.execute(text("TRUNCATE TABLE dna_memory CASCADE;"))
        conn.execute(text("TRUNCATE TABLE episodic_events CASCADE;"))
        conn.execute(text("TRUNCATE TABLE profile_facts CASCADE;"))
        conn.execute(text("TRUNCATE TABLE agent_private_memory CASCADE;"))
        conn.execute(text("TRUNCATE TABLE conversation_sessions CASCADE;"))
        conn.execute(text("TRUNCATE TABLE schedule_events CASCADE;"))
        
        # Seed basic identity (Name only) so the mentor knows who it's speaking with.
        # updated_at is NOT NULL (Python-side default in the model, bypassed by raw
        # SQL) — supply it explicitly so the INSERT doesn't violate the constraint.
        conn.execute(text(
            "INSERT INTO profile_facts (category, key, value, source, updated_at) "
            "VALUES ('identity', 'full_name', '\"Nikhil\"', 'seeded', NOW());"
        ))
        
    print("✅ Memory successfully reset! The mentor is now in pure curious discovery mode.")

if __name__ == "__main__":
    confirm = input("Are you sure you want to reset all stored memories and start fresh? (y/N): ")
    if confirm.lower().strip() == 'y':
        reset_memory()
    else:
        print("Reset cancelled.")
