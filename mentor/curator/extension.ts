/**
 * mentor/extensions/memory-writer.ts
 *
 * The memory agent's extension: the tools it curates with, and the gate that
 * keeps it inside its own domain.
 *
 * This file is loaded ONLY by the memory agent (`scripts/run_memory_curator.sh`
 * passes `--no-extensions -e mentor/extensions/memory-writer.ts`). The mentor
 * never loads it, and the memory agent never loads the mentor's extensions.
 *
 * Why the gate lives here (MEMORY.md §4)
 * -------------------------------------
 * Separation by context is not separation. Separation by **write permission** is.
 *
 *   mentor       → may write `learning/` only
 *   memory agent → may write `data/` only
 *
 * So the mentor cannot corrupt memory, the memory agent cannot touch the
 * curriculum, and neither can reach the other's domain — structurally, not by
 * convention. That is the old single-writer rule, enforced per agent.
 *
 * The tools below are the sanctioned way to write; the gate is the backstop for
 * a curator that reaches for `write` directly.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { REPO_ROOT } from "../src/identity.ts";
import {
  appendAttention,
  appendMemory,
  clearRequests,
  readRequests,
  supersedeMemory,
  writeProfileFact,
} from "../src/memory/store.ts";
import { pendingTranscript, renderTranscript, writeCursor } from "../src/memory/transcript.ts";

/** The one directory the memory agent may write. */
const DATA_PREFIX = /(^|\/)data\//;

export default function memoryWriterExtensions(pi: ExtensionAPI) {
  // -------------------------------------------------------------------------
  // The gate — `data/` only
  // -------------------------------------------------------------------------
  pi.on("tool_call", (event) => {
    if (event.toolName !== "write" && event.toolName !== "edit") return;

    const input = (event.input ?? {}) as { path?: unknown };
    const target = typeof input.path === "string" ? input.path.replace(/\\/g, "/") : "";
    if (DATA_PREFIX.test(target)) return;

    const reason =
      `memory-writer guardrail: the memory agent may only write under data/ ` +
      `(blocked "${target}"). You keep memory, nothing else — do not touch the ` +
      `curriculum, the calendar, or code. Use your memory_* tools for memory.`;
    console.warn(`[memory-writer] ${reason}`);
    return { block: true, reason };
  });

  // -------------------------------------------------------------------------
  // Reading — what the curator has not seen yet
  // -------------------------------------------------------------------------
  pi.registerTool({
    name: "curator_pending",
    label: "Pending transcript",
    description:
      "Everything said since the last curation, read forward from the cursor. Call this first. It does NOT advance the cursor — call curator_advance when you have finished writing.",
    promptSnippet: "curator_pending: the conversation since the last curation, from the cursor",
    promptGuidelines: [
      "Always call curator_pending at the start. Never curate a range you have not read.",
      "If it returns nothing, there is nothing new — write nothing and stop. Silence is the correct output when nothing was worth keeping.",
    ],
    parameters: Type.Object({
      max_chars: Type.Optional(
        Type.Number({ description: "Prompt budget for the rendered transcript (default 40000)" }),
      ),
    }),
    async execute(_toolCallId, params) {
      const { lines, next, filesRead } = pendingTranscript(REPO_ROOT);
      if (lines.length === 0) {
        writeCursor(REPO_ROOT, next);
        return {
          content: [{ type: "text", text: "Nothing new since the cursor. Write nothing." }],
          details: { ok: true, lines: 0, filesRead },
        };
      }
      const body = renderTranscript(lines, params.max_chars ?? 40000);
      return {
        content: [
          {
            type: "text",
            text:
              `${lines.length} message(s) since the cursor, across ${filesRead} session file(s).\n\n` +
              `${body}\n\n` +
              `When you have finished writing memories, call curator_advance.`,
          },
        ],
        details: { ok: true, lines: lines.length, filesRead, next },
      };
    },
  });

  pi.registerTool({
    name: "curator_advance",
    label: "Advance the cursor",
    description:
      "Mark the pending range as curated so it is never read twice. Call this ONLY after everything worth keeping has been written — it is the commit point.",
    promptSnippet: "curator_advance: commit point — after this the range is never read again",
    promptGuidelines: [
      "Call curator_advance last, after every memory_write/profile_set/attention_raise has succeeded.",
      "Do not call it if a write failed — leaving the cursor behind re-reads the range and is safe; skipping it loses memory.",
    ],
    parameters: Type.Object({}),
    async execute() {
      const { next, lines } = pendingTranscript(REPO_ROOT);
      writeCursor(REPO_ROOT, next);
      return {
        content: [
          {
            type: "text",
            text: `Cursor advanced past ${lines.length} message(s). This range will not be read again.`,
          },
        ],
        details: { ok: true, cursor: next },
      };
    },
  });

  pi.registerTool({
    name: "curator_requests",
    label: "Read remember-requests",
    description:
      "What the mentor asked you to remember explicitly ('remember this'). Drains the queue, so call it once and act on everything it returns.",
    promptSnippet: "curator_requests: explicit 'remember this' requests from the mentor",
    promptGuidelines: [
      "These were asked for by name — they take priority over anything you inferred.",
      "Attribute each one yourself: if he stated it, source is user_stated; if the mentor only inferred it, mentor_inferred. The request deliberately carries no source.",
    ],
    parameters: Type.Object({}),
    async execute() {
      const requests = readRequests(REPO_ROOT);
      if (requests.length === 0) {
        return { content: [{ type: "text", text: "No pending requests." }], details: { ok: true, count: 0 } };
      }
      const body = requests.map((r) => `- ${r.what}${r.detail ? ` — ${r.detail}` : ""}`).join("\n");
      clearRequests(REPO_ROOT);
      return {
        content: [
          {
            type: "text",
            text:
              `${requests.length} request(s), drained from the queue:\n${body}\n\n` +
              `Write each one with memory_write, attributing its source honestly.`,
          },
        ],
        details: { ok: true, count: requests.length },
      };
    },
  });

  // -------------------------------------------------------------------------
  // Writing — memory, profile, attention
  // -------------------------------------------------------------------------
  pi.registerTool({
    name: "memory_write",
    label: "Write a memory",
    description:
      "Append one memory worth keeping. Rejected without a source and a quote — if you cannot quote the sentence it came from, it is an inference you cannot support, so do not write it.",
    promptSnippet: "memory_write: append one memory (needs content, source, quote)",
    promptGuidelines: [
      "Only write a memory that is a fact, a preference/constraint, a goal, a struggle, or an observation with a repeated pattern behind it. Anything else stays in the transcript — your silence IS the forgetting.",
      "`quote` must be his or the mentor's actual words from the transcript. Never paraphrase it into a tidier claim.",
      "source=user_stated when HE said it. source=mentor_inferred when the mentor guessed — those are capped at 0.60 and the mentor must ask about them. Never label a guess as user_stated.",
      "Do not write a memory that duplicates one you already wrote in this run.",
    ],
    parameters: Type.Object({
      content: Type.String({ description: "The memory, one clear sentence." }),
      source: Type.String({
        description: "user_stated | seeded | data_derived | mentor_inferred. Attribute honestly.",
      }),
      quote: Type.String({ description: "The exact words it came from, from the transcript." }),
      memory_type: Type.Optional(
        Type.String({ description: "fact | preference | goal | struggle | observation (default fact)" }),
      ),
      confidence: Type.Optional(Type.Number({ description: "0-1. Clamped to the source's ceiling." })),
      tags: Type.Optional(Type.Array(Type.String({ description: "Short tags." }))),
    }),
    async execute(_toolCallId, params) {
      const result = appendMemory(REPO_ROOT, params);
      if (!result.ok) {
        return {
          content: [{ type: "text", text: `Rejected: ${result.error}` }],
          details: { ok: false, error: result.error },
        };
      }
      const clamped =
        result.clampedFrom !== undefined
          ? ` (confidence ${result.clampedFrom} clamped to ${result.confidence} by the source ceiling)`
          : "";
      return {
        content: [
          { type: "text", text: `Remembered as ${result.id}, confidence ${result.confidence}${clamped}.` },
        ],
        details: { ok: true, ...result },
      };
    },
  });

  pi.registerTool({
    name: "memory_supersede",
    label: "Correct a memory",
    description:
      "Replace an existing memory with a corrected one. The old row is never edited — a new memory is written and a pointer records the replacement, so nothing is lost.",
    promptSnippet: "memory_supersede: correct a memory without deleting the old one",
    promptGuidelines: [
      "Use this when something he said contradicts a memory you hold — including one you wrote earlier in this run.",
      "Never try to delete a memory. Superseding is the only correction mechanism.",
    ],
    parameters: Type.Object({
      old_id: Type.String({ description: "The id of the memory being corrected (mem_...)." }),
      content: Type.String({ description: "The corrected memory." }),
      source: Type.String({ description: "user_stated | seeded | data_derived | mentor_inferred" }),
      quote: Type.String({ description: "The exact words that correct it." }),
    }),
    async execute(_toolCallId, params) {
      const result = supersedeMemory(REPO_ROOT, params.old_id, {
        content: params.content,
        source: params.source,
        quote: params.quote,
      });
      if (!result.ok) {
        return {
          content: [{ type: "text", text: `Rejected: ${result.error}` }],
          details: { ok: false, error: result.error },
        };
      }
      return {
        content: [
          { type: "text", text: `Superseded ${params.old_id} with ${result.id}. The old row is kept.` },
        ],
        details: { ok: true, ...result },
      };
    },
  });

  pi.registerTool({
    name: "profile_set",
    label: "Set a profile fact",
    description:
      "Set one structured fact about him (identity, career, goals, skills, preferences). Use for typed facts that belong in a field rather than in prose.",
    promptSnippet: "profile_set: set a structured fact about him",
    promptGuidelines: [
      "Use this for stable, typed facts: full_name, target_roles, target_locations, long_term_goal, current_role, employment_status.",
      "Never overwrite a fact he stated with something you inferred. If it conflicts, keep the stated one and raise an attention item instead.",
    ],
    parameters: Type.Object({
      category: Type.String({
        description: "identity | career | education | goals | skills | preferences | learning | system",
      }),
      key: Type.String({ description: "The fact's key, e.g. target_roles" }),
      value_json: Type.String({ description: "The value as JSON (string, number, array, or object)." }),
    }),
    async execute(_toolCallId, params) {
      let value: unknown;
      try {
        value = JSON.parse(params.value_json);
      } catch {
        // A bare string is the common case and should not be an error.
        value = params.value_json;
      }
      writeProfileFact(REPO_ROOT, params.category, params.key, value);
      return {
        content: [{ type: "text", text: `Set ${params.category}.${params.key}.` }],
        details: { ok: true, category: params.category, key: params.key },
      };
    },
  });

  pi.registerTool({
    name: "attention_raise",
    label: "Raise something for his attention",
    description:
      "Queue a signal the MENTOR should consider — not an instruction to it. Use for an unresolved thread, a contradiction between things he has said, or a system fact worth noticing.",
    promptSnippet: "attention_raise: flag something for the mentor to consider (not an order)",
    promptGuidelines: [
      "This is influence, not control. You give the mentor something to think about; it decides whether to act, and when.",
      "Never phrase it as a command. 'He has raised the argmax step three times' is right; 'tell him to study the argmax step' is wrong.",
      "Only raise something with evidence behind it — quote what you saw.",
    ],
    parameters: Type.Object({
      kind: Type.String({ description: "unresolved_thread | contradiction | system_state" }),
      what: Type.String({ description: "The signal, stated as an observation." }),
      evidence: Type.Optional(Type.String({ description: "What you saw that supports it." })),
      suggestion: Type.Optional(
        Type.String({
          description: "An optional angle the mentor might take. A suggestion, never an instruction.",
        }),
      ),
    }),
    async execute(_toolCallId, params) {
      const allowed: AttentionKind[] = ["unresolved_thread", "contradiction", "system_state"];
      const kind = params.kind as AttentionKind;
      if (!allowed.includes(kind)) {
        const error = `unknown kind '${params.kind}'. Use one of: ${allowed.join(", ")}.`;
        return { content: [{ type: "text", text: `Rejected: ${error}` }], details: { ok: false, error } };
      }
      appendAttention(REPO_ROOT, {
        kind,
        what: params.what,
        evidence: params.evidence,
        suggestion: params.suggestion,
      });
      return {
        content: [{ type: "text", text: `Queued for the mentor's attention: ${params.what}` }],
        details: { ok: true, kind },
      };
    },
  });
}