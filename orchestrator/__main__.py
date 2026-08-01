"""
orchestrator/__main__.py

CLI chat loop for Nikhil's mentor/companion AI.
"""

from __future__ import annotations
import sys
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

from orchestrator.memory.store import MemoryManager
from orchestrator.runner import OrchestratorRunner


def main() -> None:
    print("Loading memory and ensuring schema...")
    memory_manager = MemoryManager()
    memory_manager.ensure_schema()

    runner = OrchestratorRunner(memory_manager)
    session_id = str(uuid4())
    state = runner.create_state(session_id)

    print(f"Mentor session started: {session_id}")
    print("Type your message (empty line to exit).")
    print("-" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not user_input:
            print("Goodbye.")
            break

        try:
            state = runner.run_turn(state, user_input)
        except Exception as exc:
            print(f"\nMentor (error): {exc}")
            continue

        print(f"\nMentor:\n{state['response_text']}")
        print("-" * 60)


if __name__ == "__main__":
    main()
