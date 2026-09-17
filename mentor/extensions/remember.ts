/**
 * mentor/extensions/remember.ts
 *
 * The mentor's side of the memory seam: ask for something to be remembered, and
 * read what has been.
 *
 * Why the mentor does not just write memory itself (MEMORY.md §4)
 * -------------------------------------------------------------
 * One writer per file is what makes the separation true. If the mentor could
 * write `data/memories.jsonl`, there would be two writers again — and a
 * half-written line is a corrupted store.
 *
 * So an explicit "remember this" does not write memory. It appends a **request**
 * to `data/requests.jsonl`, and the memory agent drains it into real memory with
 * proper provenance. Two writers, two files, no shared mutable state — and the
 * mentor's permission gate stays exactly as restrictive as it was.
 *
 * `read_memory` reads `data/memories.jsonl` and `data/profile.yaml` directly,
 * because reads have no such hazard. The mentor does not *remember* last week —
 * it re-reads what was promoted, every session. That is what continuity is.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { appendRequest, profileFactsFlat, readMemories } from "../src/memory/store.ts";

const REPO_ROOT = process.env.MENTOR_REPO_ROOT?.trim() || process.cwd();

/** Render a memory as one readable line, with its provenance. */
function renderMemory(m: {
  content: string;
  source: string;
  confidence: number;
  user_confirmed: boolean;
  created_at?: string;
}): string {
  const trust = m.user_confirmed
    ? "he stated it"
    : m.source === "mentor_inferred"
      ? "an inference — ASK, do not assert"
      : "derived, not confirmed";
  const when = (m.created_at ?? "").slice(0, 10);
  return `- ${m.content}\n  [${m.source} · ${m.confidence} · ${trust}${when ? ` · ${when}` : ""}]`;
}

export default function rememberExtensions(pi: ExtensionAPI) {
  pi.registerTool({
    name: "remember",
    label: "Remember this",
    description:
      "Ask for something to be kept. Use it when he says 'remember this', states something important about himself, gives you a goal or a constraint, or corrects something you had wrong. It does not write memory directly — it asks the memory agent to, and the agent attributes the source honestly.",
    promptSnippet: "remember: ask for something to be kept (he said 'remember this', or it matters)",
    promptGuidelines: [
      "Use it for things worth carrying to a future session: a fact about him, a preference, a constraint on his time, a goal, a struggle, or a correction.",
      "Do not use it for what was just discussed — if it does not need to survive this conversation, do not ask for it to be kept.",
      "Pass what he said in `what`, in his own words where you can. Do not tidy it up — the phrasing is part of the signal.",
      "This is a request, not a write. Never tell him it is saved — say you have asked for it to be.",
    ],
    parameters: Type.Object({
      what: Type.String({ description: "What should be kept, in his words where possible." }),
      why: Type.Optional(
        Type.String({
          description:
            "Why it matters — the future moment it would change. Helps the agent judge whether to keep it.",
        }),
      ),
    }),
    async execute(_toolCallId, params) {
      try {
        appendRequest(REPO_ROOT, params.what, params.why);
        return {
          content: [
            {
              type: "text",
              text:
                `Requested: it will be kept if it survives the memory agent's judgement.\n` +
                `Say "I've got that" or "I'll keep that" — never "it's saved".`,
            },
          ],
          details: { ok: true },
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return {
          content: [
            {
              type: "text",
              text: `Could not queue that to be remembered (${message}). Tell him plainly rather than implying it was kept.`,
            },
          ],
          details: { ok: false, error: message },
        };
      }
    },
  });

  pi.registerTool({
    name: "read_memory",
    label: "Read what is remembered",
    description:
      "Read the durable memory: what you have learned about him, plus the structured facts about him. Use it before claiming to know anything about his background, preferences, goals or constraints.",
    promptSnippet: "read_memory: what you have learned about him, with provenance and confidence",
    promptGuidelines: [
      "Call this before making any claim about his background, habits, goals or constraints. Never assume — read, or ask.",
      "Memories marked 'an inference — ASK, do not assert' are capped at 0.60 because nobody confirmed them. Ask about those; never state them as fact.",
      "If it returns nothing, you do not know him yet. Say so — an empty memory is a real answer, and asking is the right move.",
    ],
    parameters: Type.Object({
      query: Type.Optional(
        Type.String({
          description: "Optional substring to filter on. Omit to list everything (the store is small).",
        }),
      ),
      include_inferred: Type.Optional(
        Type.Boolean({ description: "Include unconfirmed inferences (default true, so you can ask about them)." }),
      ),
    }),
    async execute(_toolCallId, params) {
      const all = readMemories(REPO_ROOT);
      const query = (params.query ?? "").trim().toLowerCase();
      const inferredKept = params.include_inferred ?? true;

      const filtered = all.filter((m) => {
        if (!inferredKept && m.source === "mentor_inferred") return false;
        if (!query) return true;
        return `${m.content} ${(m.tags ?? []).join(" ")}`.toLowerCase().includes(query);
      });

      const facts = profileFactsFlat(REPO_ROOT);
      const factLines = Object.entries(facts)
        .map(([key, value]) => `- ${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
        .join("\n");

      if (filtered.length === 0 && !factLines) {
        return {
          content: [
            {
              type: "text",
              text:
                "Nothing remembered yet — no facts about him, and no memories.\n" +
                "Do not guess who he is. Ask, and what he tells you can be kept.",
            },
          ],
          details: { ok: true, memories: 0, facts: 0 },
        };
      }

      const sections: string[] = [];
      if (factLines) sections.push(`FACTS ABOUT HIM (structured)\n${factLines}`);
      if (filtered.length > 0) {
        sections.push(
          `MEMORIES (${filtered.length}, newest last)\n` + filtered.map((m) => renderMemory(m)).join("\n"),
        );
      }
      if (all.length > filtered.length) {
        sections.push(`(${all.length - filtered.length} further memor(y/ies) not shown — filtered out.)`);
      }

      return {
        content: [{ type: "text", text: sections.join("\n\n") }],
        details: { ok: true, memories: filtered.length, facts: Object.keys(facts).length },
      };
    },
  });
}