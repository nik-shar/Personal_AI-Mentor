/**
 * Permission gate — the safety property the old architecture got for free.
 *
 * `CLAUDE.md` decision #1: "Agents only draft/decide — they never execute side
 * effects." The Python orchestrator enforced that *by construction*: sub-agents
 * had no filesystem or shell access at all. PI does — `bash`, `read`, `write`,
 * and `edit` are on by default, and pi ships no built-in permission system
 * (pi/README.md → "Permissions & Containerization").
 *
 * So the invariant has to be code-enforced here. Gate: G4 → TypeScript.
 *
 * Design choices:
 *   - Pattern-based blocking, never an interactive prompt: the mentor runs
 *     headless (scheduler, RPC, print mode) where asking would hang the turn.
 *   - Conservative patterns only. A false block is a recoverable annoyance
 *     ("run it yourself"); a false allow on `rm -rf` is not.
 *   - Comments explain *why* each entry exists so the list stays auditable.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Destructive shell patterns. Kept deliberately narrow to avoid false blocks. */
const DESTRUCTIVE_SHELL: RegExp[] = [
  /\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r/i, // rm -rf / rm -fr
  /\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)/i, // destroys uncommitted work
  /\bmkfs(\.|\s|$)/i, // formats a filesystem
  /\bdd\s+[^\n]*of=\/dev\//i, // raw write to a device
  /\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b/i, // curl … | sh
  /:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/, // fork bomb
  /\bchmod\s+-R\s+[0-7]*7[0-7]*7\s+\//i, // world-writable root
  /\b(shutdown|reboot|halt|poweroff)\b/i,
  />\s*\/dev\/sd[a-z]/i, // clobber a disk
];

/**
 * Paths the mentor must never write to.
 *
 * `.env` matters most: it holds the API keys and the database URL for the whole
 * mentor stack. `node_modules` and `.venv` are load-bearing for both runtimes.
 *
 * The identity files were added after the "repo as vault" decision. The mentor's
 * voice (`mentor_persona.md`) and its constitution
 * (`mentor_agent_guidelines.md`) are read FRESH every turn, so a single write to
 * them silently and permanently changes how the mentor behaves — with no error,
 * no log, and no drift check (the persona test only detects copies, not
 * tampering). `.pi/settings.json` decides which extensions and skills load at
 * all, and the code directories are the mentor itself.
 *
 * This matters more than it looks: `pi/README.md` states plainly that PI has no
 * built-in permission system — "it runs with the permissions of the user and
 * process that launched it" — and the mentor frequently runs headless (RPC from
 * the dashboard, scheduler wake-ups) where nobody is present to approve. This
 * list is the only code-enforced guard there is.
 */
const PROTECTED_PATHS: RegExp[] = [
  /(^|\/)\.git\//, // repo history
  /(^|\/)node_modules\//, // vendored deps (both runtimes)
  /(^|\/)\.venv\//, // Python env
  /(^|\/)\.env$/, // secrets
  /(^|\/)\.env\.(?!example$)[^/]*$/, // .env.local etc., but allow .env.example
  /(^|\/)\.ssh\//,
  /(^|\/)\.pgdata\//, // local Postgres data directory
  // The mentor's own identity and harness — never self-modifying.
  /(^|\/)mentor_persona\.md$/,
  /(^|\/)mentor_agent_guidelines\.md$/,
  /(^|\/)\.pi\/settings\.json$/,
  /(^|\/)orchestrator\//,
  /(^|\/)api\//,
  /(^|\/)schemas\//,
  /(^|\/)mentor\//,
  /(^|\/)scripts\//,
];

/**
 * The ONLY place a direct write is allowed: the curriculum root.
 *
 * Decided deliberately when the roadmaps moved into the repo. The curriculum is
 * a git-tracked notes tree that the mentor helps write — during a study session
 * it explains a doubt and appends the explanation to the node's note. Structure
 * (ids, prerequisites, days, anchors) is not editable through these files
 * anyway; it lives in the roadmap manifest, which Python owns and validates.
 *
 * Everything else in the sandbox goes through `propose_edit` — the convention
 * the repo already documents for code ("the pen never leaves the learner").
 */
const WRITE_ALLOWLIST: RegExp[] = [
  /(^|\/)learning\/topics\//, // config.MENTOR_CURRICULUM_PATH default
  /(^|\/)learning\//, // learner notes browsable beside the curriculum
];

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", (event) => {
    const input = (event.input ?? {}) as { command?: unknown; path?: unknown };

    if (event.toolName === "bash") {
      const command = typeof input.command === "string" ? input.command : "";
      const matched = DESTRUCTIVE_SHELL.find((pattern) => pattern.test(command));
      if (matched) {
        const reason = `mentor guardrail: blocked a destructive shell command (matched ${String(matched)}). If it is genuinely needed, Nik should run it himself.`;
        console.warn(`[mentor] ${reason} command=${JSON.stringify(command)}`);
        return { block: true, reason };
      }
      return;
    }

    if (event.toolName === "write" || event.toolName === "edit") {
      const target = typeof input.path === "string" ? input.path : "";
      const normalized = target.replace(/\\/g, "/");

      const protectedMatch = PROTECTED_PATHS.find((pattern) => pattern.test(normalized));
      if (protectedMatch) {
        const reason = `mentor guardrail: ${event.toolName} to protected path "${target}" is blocked.`;
        console.warn(`[mentor] ${reason}`);
        return { block: true, reason };
      }

      const allowed = WRITE_ALLOWLIST.some((pattern) => pattern.test(normalized));
      if (!allowed) {
        const reason =
          `mentor guardrail: direct ${event.toolName} outside the curriculum is blocked. ` +
          `Only files under learning/ may be written directly. For anything else — code, ` +
          `docs, config — use propose_edit and let Nik apply it himself.`;
        console.warn(`[mentor] ${reason} path=${JSON.stringify(target)}`);
        return { block: true, reason };
      }
    }
  });
}
