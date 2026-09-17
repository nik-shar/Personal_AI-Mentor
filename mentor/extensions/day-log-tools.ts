/**
 * The day log — recording what he is actually doing, as he does it.
 *
 * Gate: G1 (Python owns every store) → sidecar write. The *policy* about when to
 * log and how to reply is G4, so it lives here.
 *
 * Why this tool exists
 * --------------------
 * Everything else the mentor owns is either a plan for his day or a claim about
 * who he is. Nothing recorded what he actually DID — which is why the store held
 * two schedule events and one anchor, and why "you usually sleep 10 to 9" was a
 * claim it could not support.
 *
 * A day log is the *record* half of memory: raw evidence, in his words, at the
 * moment it happened, from somewhere he can reach in a second — usually his
 * phone, which is why `integrations/telegram_listener.py` exists. Interpretation
 * is deliberately NOT part of it: patterns are derived from the record
 * afterwards, in code, so the arithmetic is testable and the mentor never has to
 * trust its own counting.
 *
 * The one shape that makes it work: a message that starts something CLOSES
 * whatever came before it, so a handful of one-liners ("waking up", "starting my
 * learning", "off to lunch") become durations without him ever reporting one.
 * That resolution happens in the handler, not here.
 *
 * On length, deliberately: a log is an acknowledgement. The temptation to answer
 * "starting my learning" with a study plan is exactly what would make him stop
 * logging, so the guidelines below forbid it in as many words.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { callTool } from "../src/sidecar.ts";

// ---------------------------------------------------------------------------
// Payload shape (mirrors the generated pydantic contract)
// ---------------------------------------------------------------------------

interface ClosedInterval {
  activity?: string | null;
  start?: string | null;
  end?: string | null;
  duration_min?: number | null;
}

interface DayLogPayload {
  logged: boolean;
  kind: string;
  activity?: string | null;
  at?: string | null;
  clock?: string | null;
  closed?: ClosedInterval | null;
  open_now?: boolean | null;
  duplicate?: boolean;
  message?: string;
  error?: string | null;
}

// ---------------------------------------------------------------------------
// Formatting — the duration math already happened in Python; never redo it here
// ---------------------------------------------------------------------------

function formatDayLog(payload: DayLogPayload): string {
  if (payload.error) {
    return `Not logged: ${payload.error}`;
  }
  const lines = [payload.message || `Logged ${payload.kind}${payload.clock ? ` at ${payload.clock}` : ""}.`];
  if (payload.duplicate) {
    lines.push(
      "This exact entry was already recorded moments ago — it was not logged twice. Do not treat it as a second event.",
    );
  }
  if (payload.open_now) {
    lines.push("An interval is now open. Nothing more to record until his next message.");
  }
  return lines.join("\n");
}

export default function mentorDayLogTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "log_day_event",
    label: "Log a moment of the day",
    description:
      "Record a moment of his day in his own words — waking up, starting or switching an activity, lunch, going to sleep — and get back the duration of the interval it closed. Use it whenever he is telling you what he is doing, rather than asking you something.",
    promptSnippet:
      "log_day_event: record what he is doing right now, in his words, and get the closed interval's duration",
    promptGuidelines: [
      "When he narrates his own day — 'waking up', 'starting my learning', 'off to lunch', 'applying to jobs now', 'going to sleep' — call log_day_event. These are records, not questions.",
      "Pass what he is doing in HIS words in `activity`. Never tidy it into a category: his phrasing is itself the signal, and a neat label throws it away.",
      "Omit `at` for 'right now' — the arrival time of his message is the timestamp. Never invent or guess a time he did not give you.",
      "Put anything else he said into `note` verbatim, including vagueness like 'exhausted' or 'will do something random'. Do not discard it, and do not try to interpret it in the call.",
      "A log is an acknowledgement: reply in ONE line. Do not turn it into a plan, a lecture, or an unsolicited action plan — that is what would make him stop logging.",
      "You may ask at most ONE short question after logging, and only when the answer changes the record (he said 'learning' and the subject genuinely matters later).",
      "Never say you have logged something unless log_day_event returned logged: true in this turn.",
    ],
    parameters: Type.Object({
      kind: Type.String({
        description:
          "What kind of moment this is: 'wake' (up for the day), 'start' (beginning an activity), 'switch' (moving to a different one), 'break' (stepping away), 'done' (finished for now), 'sleep' (going to bed), or 'note' (an aside that changes nothing).",
      }),
      activity: Type.Optional(
        Type.String({
          description:
            "What he is doing, in HIS words — 'learning', 'applying to jobs', 'outreach'. Never normalised into a tidier category.",
        }),
      ),
      at: Type.Optional(
        Type.String({
          description:
            "Wall-clock ISO datetime in his timezone, only when he named a time. Omit for 'right now'.",
        }),
      ),
      note: Type.Optional(
        Type.String({
          description:
            "Anything else he said, verbatim — 'exhausted', 'nothing to do, will do something random'.",
        }),
      ),
      energy: Type.Optional(
        Type.Number({ description: "1-5, only when he actually states it." }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<DayLogPayload>("log_day_event", params);
      if (!result.ok) {
        return {
          content: [{ type: "text", text: result.error }],
          details: { ok: false, error: result.error },
        };
      }
      return {
        content: [{ type: "text", text: formatDayLog(result.data) }],
        details: { ...result.data, ok: Boolean(result.data?.logged) },
      };
    },
  });
}