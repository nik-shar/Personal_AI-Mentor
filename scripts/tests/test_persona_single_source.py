"""
scripts/tests/test_persona_single_source.py

Verification suite for the mentor persona's single-source contract:

  1. The persona text lives in `mentor_persona.md`, NOT in persona.py
  2. The reader parses all three sections and renders them in order
  3. `{name}` is substituted from the same config value as before
  4. build_persona_block still assembles voice + hard rules (+ examples)
  5. Fail-open: a missing file degrades to empty blocks, loudly, no crash
  6. Both runtimes read the same file (the drift this file exists to prevent)

Run from project root:
    uv run python scripts/tests/test_persona_single_source.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

import orchestrator.config as config
from orchestrator.cognition import persona

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  {detail}")


def main() -> None:
    # ------------------------------------------------------------------
    print("\n[1] The content lives in the markdown file, not in the code")
    source = Path(persona.__file__).read_text(encoding="utf-8")
    check("persona.py contains no 'YOUR VOICE:' block", "YOUR VOICE:" not in source)
    check("persona.py contains no 'HARD RULES:' block", "HARD RULES:" not in source)
    check("persona.py contains no example exchanges", "HOW THIS SOUNDS IN PRACTICE" not in source)

    md = Path(persona.MENTOR_PERSONA_PATH)
    check("mentor_persona.md exists", md.is_file(), str(md))
    check("mentor_persona.md is the default path", md.parent == ROOT, str(md))

    # ------------------------------------------------------------------
    print("\n[2] Section parsing and rendering")
    sections = persona._sections()
    check("parses sections 1, 2, 3", sorted(sections) == ["1", "2", "3"], str(sorted(sections)))
    check("voice rules render", persona.voice_rules().startswith("YOUR VOICE:"))
    check("hard rules render", persona.hard_rules().startswith("HARD RULES:"))
    check("examples render", persona.few_shot_examples().startswith("HOW THIS SOUNDS IN PRACTICE:"))

    # ------------------------------------------------------------------
    print("\n[3] {name} substitution comes from config")
    original_name = config.USER_NAME
    try:
        config.USER_NAME = "Ada"
        check("substitutes the configured name", "first person to Ada:" in persona.voice_rules())
        check("leaves no placeholder behind", "{name}" not in persona.voice_rules())
        check("substitutes in examples too", "Ada: hey" in persona.few_shot_examples())
    finally:
        config.USER_NAME = original_name
    check("restores the configured name", f"first person to {original_name}:" in persona.voice_rules())

    # ------------------------------------------------------------------
    print("\n[4] build_persona_block assembly is unchanged")
    block = persona.build_persona_block()
    check("starts with the voice rules", block.startswith(persona.voice_rules()))
    check("contains the hard rules", persona.hard_rules() in block)
    check("wraps examples in <examples>", "<examples>" in block and "</examples>" in block)
    no_examples = persona.build_persona_block(include_examples=False)
    check("omits examples when asked", "<examples>" not in no_examples)
    check(
        "no-examples form is voice + hard rules only",
        no_examples == "\n".join([persona.voice_rules(), "", persona.hard_rules()]),
    )

    # ------------------------------------------------------------------
    print("\n[5] Fail-open when the file is missing")
    original_path = persona.MENTOR_PERSONA_PATH
    persona.MENTOR_PERSONA_PATH = "/nonexistent/mentor_persona.md"
    try:
        check("missing file → empty voice rules", persona.voice_rules() == "")
        check("missing file → empty hard rules", persona.hard_rules() == "")
        check("missing file → empty examples", persona.few_shot_examples() == "")
        check("missing file → no crash building the block", persona.build_persona_block() == "\n\n")
    finally:
        persona.MENTOR_PERSONA_PATH = original_path
        persona.reset_cache()
    check("restores after fail-open", persona.voice_rules().startswith("YOUR VOICE:"))

    # ------------------------------------------------------------------
    print("\n[6] Both runtimes read the same file (no drift)")
    ts_reader = ROOT / "mentor" / "src" / "identity.ts"
    check("the PI-side reader exists", ts_reader.is_file())
    ts_source = ts_reader.read_text(encoding="utf-8")
    check("the PI reader points at mentor_persona.md", "mentor_persona.md" in ts_source)
    check(
        "the PI reader holds no copy of the voice text",
        "YOUR VOICE:" not in ts_source and "HARD RULES:" not in ts_source,
    )
    extension = (ROOT / "mentor" / "extensions" / "mentor-identity.ts").read_text(encoding="utf-8")
    check(
        "the PI extension holds no copy either",
        "YOUR VOICE:" not in extension and "HARD RULES:" not in extension,
    )

    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}\nRESULT: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
