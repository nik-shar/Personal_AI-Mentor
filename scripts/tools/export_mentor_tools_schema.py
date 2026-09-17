"""
scripts/tools/export_mentor_tools_schema.py

Generate the TypeScript tool descriptors for the PI mentor bridge from the
FastAPI/pydantic tool manifest — the single source of truth.

    Python contract (api/tools.py, pydantic models)
        -> GET /tools/manifest
        -> mentor/src/generated/mentor-tools.ts   (this script)

Run after changing any tool model:

    uv run python scripts/tools/export_mentor_tools_schema.py

`mentor/extensions/read-tools.ts` imports the generated module and self-checks
its registered tools against it at session start, warning on drift. That check
is what keeps the two runtimes from silently disagreeing about the contract.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from api.tools import build_manifest

OUTPUT_PATH = ROOT_DIR / "mentor" / "src" / "generated" / "mentor-tools.ts"

HEADER = '''/**
 * AUTO-GENERATED — do not edit by hand.
 *
 * Source: api/tools.py (pydantic tool manifest)
 * Regenerate: uv run python scripts/tools/export_mentor_tools_schema.py
 */

export type MentorToolKind = "read" | "write";

export interface MentorToolDescriptor {
  /** Tool name as registered with pi.registerTool(). */
  name: string;
  /** The mentor question this tool answers. */
  intent: string;
  description: string;
  kind: MentorToolKind;
  /** JSON Schema of the tool parameters (generated from pydantic). */
  params: Record<string, unknown>;
  /** JSON Schema of the tool result (generated from pydantic). */
  result: Record<string, unknown>;
}

'''


def render_manifest_module() -> str:
    """Render the generated TypeScript module for the current manifest."""
    manifest = build_manifest()
    tools_json = json.dumps(
        [tool.model_dump() for tool in manifest.tools],
        indent=2,
        ensure_ascii=False,
    )
    return (
        HEADER
        + f"export const MENTOR_TOOLS_SERVICE = {json.dumps(manifest.service)};\n"
        + f"export const MENTOR_TOOLS_VERSION = {json.dumps(manifest.version)};\n"
        + f"export const MENTOR_TOOLS_GENERATED_AT = {json.dumps(datetime.now(timezone.utc).isoformat())};\n"
        + "\n"
        + "export const MENTOR_TOOLS: MentorToolDescriptor[] = "
        + tools_json
        + ";\n"
    )


def main() -> int:
    manifest = build_manifest()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_manifest_module(), encoding="utf-8")
    print(f"[export_mentor_tools_schema] wrote {OUTPUT_PATH}")
    print(f"[export_mentor_tools_schema] tools: {', '.join(t.name for t in manifest.tools)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
