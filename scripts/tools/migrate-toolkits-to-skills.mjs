#!/usr/bin/env node
/**
 * migrate-toolkits-to-skills.mjs
 *
 * Convert the `toolkits/` instruction sets into PI Agent Skills.
 *
 *     toolkits/<name>/{toolkit.md,philosophy.md,behaviors.md,guardrails.md}
 *         + toolkits/<name>/workflows/*.md
 *             ↓
 *     mentor/skills/<name>/SKILL.md
 *         + mentor/skills/<name>/references/workflows/<wf>.md
 *
 * Why a converter instead of hand-copying: the toolkit prose IS the asset and
 * must survive intact. A converter that strips frontmatter and re-orders
 * sections is reviewable, re-runnable, and cannot silently drop a workflow.
 *
 * Progressive disclosure (Agent Skills standard):
 *   - SKILL.md holds role + philosophy + behaviors + guardrails (the rules that
 *     apply while the skill is active) plus a workflow index.
 *   - references/workflows/*.md holds the step-by-step procedures, read by the
 *     model only when that workflow is invoked.
 *
 * Run:
 *
 *     npm run skills
 *
 * Generated files are build output: edit `toolkits/` and re-run, never edit
 * `mentor/skills/` by hand.
 *
 * Ported from the Python original when the Python layer was removed. Behaviour
 * is intentionally identical — including the failure modes, which are the point:
 * a missing file, an invalid skill name, or a tool that does not exist must
 * abort the build rather than produce a vague skill that lies about what the
 * mentor can do.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";

const ROOT_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const TOOLKITS_DIR = join(ROOT_DIR, "toolkits");
const SKILLS_DIR = join(ROOT_DIR, "mentor", "skills");

const SKILL_NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const MAX_DESCRIPTION_CHARS = 1024;

const generatedNote = (name) =>
  `<!-- AUTO-GENERATED from toolkits/${name}/ by ` +
  `scripts/tools/migrate-toolkits-to-skills.mjs — edit the toolkit, not this file. -->`;

/** Read a file, failing loudly — this is a build script, not runtime. */
function readRequired(path) {
  if (!existsSync(path)) {
    throw new Error(`[skills] missing required file: ${path}`);
  }
  return readFileSync(path, "utf8");
}

/** Split a markdown file into { meta, body }. Fails on bad YAML or no closing fence. */
function parseFrontmatter(text) {
  const stripped = text.replace(/^\uFEFF/, "");
  if (!stripped.startsWith("---")) {
    return { meta: {}, body: text };
  }
  const end = stripped.indexOf("\n---", 3);
  if (end === -1) {
    throw new Error("[skills] unterminated frontmatter block");
  }
  const meta = parseYaml(stripped.slice(3, end)) ?? {};
  if (typeof meta !== "object" || Array.isArray(meta)) {
    throw new Error("[skills] frontmatter is not a mapping");
  }
  return { meta, body: stripped.slice(end + 4).replace(/^\n+/, "") };
}

/** Collapse all whitespace runs to single spaces (for YAML-safe one-liners). */
function collapse(text) {
  return String(text ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

/**
 * Drop a body's own leading `# Heading` line.
 *
 * Each toolkit file repeats the section title as an H1 (e.g. philosophy.md
 * starts with `# Philosophy`). Inlining those under our own `##` sections would
 * produce a nested duplicate heading, which reads as noise in a file the model
 * is meant to follow.
 */
function stripLeadingHeading(body) {
  const lines = String(body ?? "")
    .replace(/^\s+/, "")
    .split("\n");
  if (lines.length > 0 && lines[0].startsWith("# ")) {
    lines.shift();
    while (lines.length > 0 && lines[0].trim() === "") {
      lines.shift();
    }
  }
  return lines.join("\n").trim();
}

// ---------------------------------------------------------------------------
// The tool registry
//
// This used to be read from a Python sidecar's pydantic manifest. The sidecar is
// gone: every tool is now a PI extension under `mentor/extensions/`, backed by
// files under `data/` and `learning/`.
//
// It is declared here, explicitly, as the single list the generated skills are
// validated against — which is what keeps a skill from claiming a tool that does
// not exist, or denying one that does (the bug this check was written to catch).
//
// Keep in sync with `mentor/extensions/*.ts`.
// ---------------------------------------------------------------------------

const TOOL_REGISTRY = {
  // memory — data/profile.yaml, data/memories.jsonl, data/episodes/
  get_identity:
    "Who he is, and what you have learned about him — structured facts plus conversation memories, each with provenance and confidence.",
  get_profile: "Read structured profile facts (identity, career targets, goals, learning state).",
  get_history: "What happened — the rolling thread, recent days, past conversations, and his day log.",
  recall_memories: "Search his memories for what he said or did around a topic.",
  // learning — learning/topics/*/roadmap.yaml
  available_topic_nodes:
    "What he can study right now: in-progress nodes first, then nodes whose prerequisites are all done.",
  mark_topic_done: "Mark a topic finished, and see which topics that unlocked.",
  log_learning_session: "Record what he studied today and advance or reset his streak.",
  // calendar — data/schedule/
  get_day_grid: "The 48-slot day grid with slot states, code-computed free windows, and the clock.",
  find_available_slots: "Candidate placement windows for a duration, computed from the grid.",
  place_time_block:
    "Book one validated block. Code-enforced: 30-minute alignment, overlap check, and an anchor guard that refuses to place tasks over sleep/meal/commute/gym.",
  set_anchor: "Reserve contiguous slots as a recurring life anchor that tasks can never overwrite.",
  get_momentum: "Streak, completion rates, and momentum trend computed fresh from schedule events.",
  save_daily_plan: "Persist today's plan and mirror its timed items onto the calendar grid.",
  log_day_event:
    "Record a moment of his day in his own words — waking up, starting or switching an activity, lunch, going to sleep — and get back the interval it closed.",
  // memory agent only — mentor/extensions/memory-writer.ts
  curator_pending: "Everything said since the last curation, read forward from the cursor.",
  curator_advance: "Mark the pending range as curated. The commit point — nothing after it re-reads that range.",
  curator_requests: "Explicit 'remember this' requests from the mentor. Drains the queue.",
  memory_write: "Append one memory, with its source and the quote it came from.",
  memory_supersede: "Correct a memory: write the replacement and point at the old one. Nothing is deleted.",
  profile_set: "Set one structured fact about him (identity, career, goals, skills, preferences).",
  attention_raise:
    "Queue an observation for the mentor to consider — a signal, never an instruction.",
};

// Pure computation in TypeScript (mentor/src/pure/). No file or network hop, so
// they are labelled differently in the generated section.
const NATIVE_TOOLS = {
  trim_plan_to_fit: "drop the lowest-priority items until a plan fits the budget (runs locally, no round-trip)",
  compute_learning_streak: "the streak arithmetic, computed locally rather than fetched",
};

/** Fail the build if a skill references a tool that does not exist. */
function validateSkillTools() {
  const known = new Set([...Object.keys(TOOL_REGISTRY), ...Object.keys(NATIVE_TOOLS)]);
  for (const [skill, tools] of Object.entries(SKILL_TOOLS)) {
    const unknown = tools.filter((tool) => !known.has(tool));
    if (unknown.length > 0) {
      throw new Error(
        `[skills] skill '${skill}' references unknown tool(s): ${unknown.join(", ")}. ` +
          `Known: ${[...known].sort().join(", ")}`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Per-skill metadata that must be written by a human
//
// The Agent Skills standard uses `description` to decide *when* to load a skill,
// so it has to name the trigger, not just the topic. And every workflow in these
// toolkits references tool names — some of which may not exist yet. Claiming an
// action the mentor cannot take is worse than having no skill at all, so each
// skill declares exactly what is possible today.
//
// Both maps are checked for full coverage: a new toolkit without an entry fails
// this script loudly instead of silently generating a vague, misleading skill.
// ---------------------------------------------------------------------------

const USE_WHEN = {
  "learning-companion":
    "Use whenever Nik is learning, stuck on a concept, practicing, reading unfamiliar " +
    "material, debugging, designing, or reviewing — whenever the goal is that he " +
    "understands rather than that the answer appears. Also use when he explicitly asks to " +
    "be taught, tutored, or quizzed.",
  "code-explorer":
    "Use whenever the conversation is about Nik's ACTUAL code — his repo, a file, a " +
    "traceback, a failing test, a diff, or a feature he is building. Read the real files " +
    "before reasoning about them.",
  "calendar-manager":
    "Use whenever the conversation touches a calendar, schedule, availability, time block, " +
    "free window, or a life anchor (sleep, meals, commute, gym).",
  "tutorial-writer":
    "Use whenever Nik asks for a written tutorial, deep note, walkthrough, or study " +
    "material on a topic — anything meant to be read later rather than discussed now.",
  "repo-architect":
    "Use whenever Nik points at a repository and wants to understand it, rebuild it, or " +
    "learn what it would take to work in it — any request to turn a codebase into " +
    "concepts, a curriculum, a roadmap, or a study plan. Also use when he asks what he " +
    "would need to learn for a project, or where his gaps are for one.",
  "memory-keeper":
    "Use when curating the conversation into memory: reading what was said, deciding what " +
    "is worth keeping, and writing it with provenance. Runs as its own agent, after a " +
    "session or at compaction — never as part of the mentor's reply.",
};

/** Which real tools each skill leans on. Every name is validated at build time. */
const SKILL_TOOLS = {
  "learning-companion": [
    "recall_memories",
    "get_profile",
    "available_topic_nodes",
    "log_learning_session",
    "compute_learning_streak",
    "trim_plan_to_fit",
  ],
  "code-explorer": ["get_profile", "recall_memories"],
  "calendar-manager": [
    "get_day_grid",
    "find_available_slots",
    "place_time_block",
    "set_anchor",
    "get_momentum",
  ],
  "tutorial-writer": ["get_profile", "available_topic_nodes", "recall_memories"],
  "repo-architect": [
    "get_identity",
    "recall_memories",
    "available_topic_nodes",
    "mark_topic_done",
    "get_day_grid",
    "find_available_slots",
    "place_time_block",
  ],
  "memory-keeper": [
    "curator_requests",
    "curator_pending",
    "memory_write",
    "memory_supersede",
    "profile_set",
    "attention_raise",
    "curator_advance",
  ],
};

/** Built-in PI tools worth naming for a given skill. */
const SKILL_BUILTINS = {
  "learning-companion": ["read"],
  "code-explorer": ["read", "grep", "find", "ls", "bash"],
  "calendar-manager": [],
  "tutorial-writer": ["read", "grep", "find"],
  "repo-architect": ["read", "grep", "find", "ls", "bash"],
  "memory-keeper": ["read", "grep"],
};

// Hand-written, and ONLY for capabilities that genuinely do not exist yet.
// Everything else about tool availability is generated, so this is the one place
// that needs a human when a new tool lands.
const SKILL_GAPS = {
  "learning-companion":
    "**No learning-log reader yet.** Assemble retrieval practice material yourself from " +
    "`recall_memories` + `available_topic_nodes` + `data/memories.jsonl`. If those return " +
    "nothing, say you don't have enough history to quiz him on — **never invent history.**",
  "code-explorer": "",
  "calendar-manager": "",
  "tutorial-writer":
    "**Notes go under `learning/topics/`, not the Obsidian vault.** Write them there and say " +
    "where they landed — never claim a file was written unless it actually was.",
  "repo-architect":
    "**Estimates do not self-calibrate yet.** Hour figures are your judgement on the day you " +
    "wrote them; nothing reads back the real time he logged against a node to adjust them. " +
    "Say so when you present them, and when he overruns a topic consistently, revise the " +
    "estimate out loud rather than quietly re-planning.",
  "memory-keeper":
    "**You run when something triggers you, not continuously.** The mentor's compaction and " +
    "shutdown hooks are not wired yet, so curation is currently a manual or cron invocation " +
    "(`scripts/run_memory_curator.sh`). The cursor makes a late run safe — nothing is skipped — " +
    "but no memory is written until that command runs.",
};

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/** Render the 'Tools available right now' section for one skill. */
function renderToolsSection(skill) {
  const declared = SKILL_TOOLS[skill];
  const mentorTools = declared.filter((tool) => !(tool in NATIVE_TOOLS));
  const native = declared.filter((tool) => tool in NATIVE_TOOLS);

  const lines = [];
  if (mentorTools.length > 0) {
    lines.push("Mentor tools (PI extensions, backed by files under `data/` and `learning/`):");
    for (const tool of mentorTools) {
      lines.push(`- \`${tool}\` — ${TOOL_REGISTRY[tool]}`);
    }
  }
  if (native.length > 0) {
    if (lines.length > 0) lines.push("");
    lines.push("Pure local tools (no I/O — computed in-process, no file or network hop):");
    for (const tool of native) {
      lines.push(`- \`${tool}\` — ${NATIVE_TOOLS[tool]}`);
    }
  }

  const builtins = SKILL_BUILTINS[skill] ?? [];
  if (builtins.length > 0) {
    if (lines.length > 0) lines.push("");
    lines.push("Built-in PI tools available here: " + builtins.map((t) => `\`${t}\``).join(", "));
  }

  const gap = (SKILL_GAPS[skill] ?? "").trim();
  if (gap) {
    lines.push("");
    lines.push(`**Still missing:** ${gap}`);
  }
  return lines.join("\n");
}

/** Render one SKILL.md from the toolkit's four always-on files + workflow index. */
function renderSkillMd(name, meta, activateBody, philosophy, behaviors, guardrails, workflows) {
  const title = collapse(meta.title ?? name);
  const role = String(meta.role ?? "").trim();
  const baseDescription = collapse(meta.description ?? "");
  const useWhen = collapse(USE_WHEN[name]);
  const description = `${baseDescription} ${useWhen}`;

  if (description.length > MAX_DESCRIPTION_CHARS) {
    throw new Error(
      `[skills] '${name}' description is ${description.length} chars ` +
        `(max ${MAX_DESCRIPTION_CHARS}) — shorten USE_WHEN`,
    );
  }

  const indexRows = workflows
    .map(([wf, desc]) => `| \`${wf}\` | ${collapse(desc)} | \`references/workflows/${wf}.md\` |`)
    .join("\n");

  return (
    [
      "---",
      `name: ${name}`,
      "description: >-",
      `  ${description}`,
      "---",
      "",
      `# ${title}`,
      "",
      generatedNote(name),
      "",
      "## When this applies",
      "",
      stripLeadingHeading(activateBody),
      "",
      "## Role",
      "",
      role,
      "",
      "## Philosophy",
      "",
      stripLeadingHeading(philosophy),
      "",
      "## Behaviors",
      "",
      stripLeadingHeading(behaviors),
      "",
      "## Guardrails",
      "",
      stripLeadingHeading(guardrails),
      "",
      "## Tools available right now",
      "",
      renderToolsSection(name),
      "",
      "## Workflows",
      "",
      "Load the matching workflow file before doing the work — its path is relative to",
      "this skill's directory. Do not improvise a procedure when a workflow exists.",
      "",
      "| Workflow | Use when | File |",
      "| --- | --- | --- |",
      indexRows,
      "",
    ].join("\n")
  );
}

/** Build one skill directory. Returns { name, workflowCount, skillChars }. */
function buildSkill(toolkitDir) {
  const name = basename(toolkitDir);

  if (!SKILL_NAME_RE.test(name)) {
    throw new Error(
      `[skills] '${name}' is not a valid Agent Skills name ` +
        "(lowercase letters, digits, single hyphens)",
    );
  }
  if (!(name in USE_WHEN) || !(name in SKILL_TOOLS)) {
    throw new Error(
      `[skills] toolkit '${name}' has no USE_WHEN/SKILL_TOOLS entry. ` +
        "Add both before migrating — the description must name the trigger, and the skill " +
        "must declare which of its tools it relies on.",
    );
  }

  const { meta, body: activateBody } = parseFrontmatter(readRequired(join(toolkitDir, "toolkit.md")));
  const { body: philosophy } = parseFrontmatter(readRequired(join(toolkitDir, "philosophy.md")));
  const { body: behaviors } = parseFrontmatter(readRequired(join(toolkitDir, "behaviors.md")));
  const { body: guardrails } = parseFrontmatter(readRequired(join(toolkitDir, "guardrails.md")));

  const workflowsDir = join(toolkitDir, "workflows");
  const workflowFiles = readdirSync(workflowsDir)
    .filter((f) => f.endsWith(".md"))
    .sort();

  const workflows = [];
  for (const filename of workflowFiles) {
    const stem = filename.replace(/\.md$/, "");
    const { meta: wfMeta, body: wfBody } = parseFrontmatter(
      readRequired(join(workflowsDir, filename)),
    );
    workflows.push([stem, String(wfMeta.description ?? "").trim()]);

    const target = join(SKILLS_DIR, name, "references", "workflows", filename);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(
      target,
      `<!-- AUTO-GENERATED from toolkits/${name}/workflows/${filename} -->\n\n${wfBody.trim()}\n`,
      "utf8",
    );
  }

  const skillMd = renderSkillMd(name, meta, activateBody, philosophy, behaviors, guardrails, workflows);
  const targetDir = join(SKILLS_DIR, name);
  mkdirSync(targetDir, { recursive: true });
  writeFileSync(join(targetDir, "SKILL.md"), skillMd, "utf8");

  return { name, workflowCount: workflows.length, skillChars: skillMd.length };
}

function main() {
  if (!existsSync(TOOLKITS_DIR) || !statSync(TOOLKITS_DIR).isDirectory()) {
    throw new Error(`[skills] no toolkits dir at ${TOOLKITS_DIR}`);
  }

  // A skill may not reference a tool that does not exist. Run before writing.
  validateSkillTools();

  const toolkitDirs = readdirSync(TOOLKITS_DIR)
    .filter((entry) => statSync(join(TOOLKITS_DIR, entry)).isDirectory())
    .sort();

  if (toolkitDirs.length === 0) {
    throw new Error("[skills] no toolkits found");
  }

  console.log(`[skills] ${TOOLKITS_DIR} -> ${SKILLS_DIR}`);

  for (const entry of toolkitDirs) {
    // Regenerate from scratch: stale workflow files from a renamed or deleted
    // workflow must not linger and stay loadable.
    const skillDir = join(SKILLS_DIR, entry);
    if (existsSync(skillDir)) {
      rmSync(skillDir, { recursive: true, force: true });
    }
    const { name, workflowCount, skillChars } = buildSkill(join(TOOLKITS_DIR, entry));
    console.log(`  ${name}: ${workflowCount} workflows, SKILL.md ${skillChars} chars`);
  }

  console.log("[skills] done — edit toolkits/, never mentor/skills/");
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}