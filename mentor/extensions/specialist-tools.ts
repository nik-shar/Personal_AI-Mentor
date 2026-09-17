/**
 * Phase 4 — specialist routing (wiring only).
 *
 * The reasoning loop's *routing* half — "does this turn need goal_decomposer,
 * job_hunter, or linkedin_writer?" — moves here, because that is a policy
 * decision and policy is gate G4 → TypeScript. The specialists themselves do not
 * move: all three are G1/G2 (vault writes, tectonic/pypdf, the Chroma voice
 * store), so they stay Python, permanently.
 *
 * One tool reaches all three through the sidecar, which runs the same three steps
 * the graph's dispatch → executor → merger path ran (`build_task` → `spec.run` →
 * `apply_memory_delta`). So this is a routing seam, not a re-implementation — and
 * it is what stops `orchestrator.py` from being load-bearing for specialists.
 *
 * The logic lives in `src/specialist.ts` (testable); this file only registers the
 * tool and declares the model-facing routing policy.
 *
 * On the schema: the tool name and parameter schema come from the generated
 * contract (`src/generated/mentor-tools.ts` ← `api/tools.py`), so they are NOT
 * hand-tuned here (boundary rule 4). The `description` and `promptGuidelines` ARE
 * policy — and per finding 3, the description is what actually triggers a tool,
 * which is why the valid agents are named in it.
 */

import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { callTool } from "../src/sidecar.ts";
import {
  formatSpecialist,
  isSuccess,
  specialistTimeoutMs,
  type SpecialistPayload,
} from "../src/specialist.ts";

export default function mentorSpecialistTools(pi: ExtensionAPI) {
  const timeoutMs = specialistTimeoutMs(process.env);

  pi.registerTool({
    name: "run_specialist",
    label: "Run a specialist",
    description:
      "Hand roadmap/tutorial work to goal_decomposer, resume or application work to job_hunter, or a post draft to linkedin_writer. These create real files and vault notes, so they only run when he actually asked.",
    promptSnippet:
      "run_specialist: delegate a roadmap, resume/application, or LinkedIn-post request to its specialist",
    promptGuidelines: [
      "Call run_specialist for exactly three kinds of work: learning roadmaps or tutorials (goal_decomposer), resume tailoring / application tracking / job-fit questions (job_hunter), and LinkedIn post drafts (linkedin_writer). Everything else you do yourself.",
      "The request is the consent. These specialists write real files, so run one when he asked for it — never speculatively because it seems helpful.",
      "Pass his ask in his own words in `request`. Never paraphrase away the specifics he gave: timeframe, hours per day, company, role title, topic.",
      "Omit task_type unless he named the action. job_hunter is multi-action and resolves intent from `request` far better than a fixed default.",
      "One specialist per turn unless he explicitly asks for a chain (for example: build the roadmap, then find matching jobs).",
      "Never say a roadmap, resume, tailored file, or post draft exists unless run_specialist returned a success or partial status in this turn.",
      "linkedin_writer returns a DRAFT for him to review — never tell him it was posted.",
    ],
    parameters: Type.Object({
      agent: Type.String({
        description:
          "Which specialist: 'goal_decomposer' (learning roadmaps and tutorials), 'job_hunter' (resume tailoring, application pipeline, fit assessment), or 'linkedin_writer' (post drafts).",
      }),
      request: Type.String({
        description:
          "What he asked for, in his own words. Becomes the specialist's user request verbatim.",
      }),
      task_type: Type.Optional(
        Type.String({
          description:
            "Optional explicit action (e.g. tailor_resume, log_application, search_jobs). Omit to let the specialist resolve intent from `request`.",
        }),
      ),
      session_id: Type.Optional(
        Type.String({ description: "Session id; used for tracing on the Python side." }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await callTool<SpecialistPayload>("run_specialist", params, { timeoutMs });
      if (!result.ok) {
        return {
          content: [{ type: "text", text: result.error }],
          details: { ok: false, error: result.error },
        };
      }
      return {
        content: [{ type: "text", text: formatSpecialist(result.data) }],
        details: { ...result.data, ok: isSuccess(result.data) },
      };
    },
  });
}
