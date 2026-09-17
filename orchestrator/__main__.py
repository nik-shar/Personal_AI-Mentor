"""
orchestrator/__main__.py

CLI chat loop for Nikhil's mentor/companion AI.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from orchestrator.memory.store import MemoryManager
from orchestrator.orchestrator import close_active_session
from orchestrator.runner import OrchestratorRunner

# One continuous conversation thread, resumed across CLI runs — the mentor is
# a relationship, not a general chatbot, so sessions never start from zero.
MAIN_THREAD_ID = "main_thread"


def main() -> None:
    print("Loading memory and ensuring schema...")
    memory_manager = MemoryManager()
    memory_manager.ensure_schema()

    runner = OrchestratorRunner(memory_manager)
    session_id = MAIN_THREAD_ID
    state = runner.create_state(session_id)

    # Resume the persistent thread: seed the live transcript + make the gap
    # detector aware of the last activity so the day-boundary rollup fires
    # naturally on the first turn of a new day.
    try:
        from orchestrator.memory.conversation_rollup import resume_thread
        thread = resume_thread(memory_manager)
        transcript = thread.get("transcript") or []
        if transcript:
            state["working_memory"]["conversation_history"] = transcript
            state["working_memory"]["last_turn_at"] = transcript[-1].get("timestamp")
            print(f"Mentor resumed continuous conversation ({len(transcript)} recent turns).")
    except Exception as exc:
        print(f"Warning: conversation thread resume failed: {exc}")

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

        print("🧠 Mentor is thinking...", end="", flush=True)
        import time
        start_t = time.time()
        try:
            state = runner.run_turn(state, user_input)
            elapsed = time.time() - start_t
            print(f"\r⏱️  Completed in {elapsed:.2f}s" + " " * 30)
        except Exception as exc:
            print(f"\r\nMentor (error): {exc}")
            continue

        print(f"\nMentor:\n{state['response_text']}")
        print("-" * 60)

    # Persist the session transcript (with timestamps) before exiting.
    saved = close_active_session(state, memory_manager)
    if saved:
        print(f"Session transcript saved ({saved} turns).")


if __name__ == "__main__":
    main()
