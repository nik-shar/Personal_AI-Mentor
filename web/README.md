# AI Mentor — Next.js Dashboard

A modern Next.js (App Router) front-end for the Personal AI Mentor. It replaces
the legacy vanilla `ui/` dashboard and talks to the existing **FastAPI** backend
(`api/main.py`) — no backend changes required.

## Why Next.js

- Route-based pages, shared layout, and reusable components instead of one big `app.js`.
- TypeScript contracts mirrored from the FastAPI responses (`src/lib/types.ts`).
- Server-side **rewrites proxy** `/api/*` → FastAPI, so there is a single origin
  (no CORS) and the backend host lives in one place.
- Easy path to more functionality: streaming chat, server components, auth,
  data caching, etc.

## Pages

| Route | Purpose | Backend endpoints |
|-------|---------|-------------------|
| `/` | Overview dashboard (stats + quick links) | `/api/health`, `/api/roadmaps`, `/api/applications`, `/api/memories`, `/api/schedule` |
| `/chat` | Mentor chat with pipeline badges + markdown | `POST /api/chat`, `POST /api/session/end` |
| `/flow` | **Architecture visualizer** — nodes/tools light up per message | `GET /api/architecture`, `POST /api/chat` (returns `trace`) |
| `/roadmaps` | Roadmap cards + Obsidian index reader | `/api/roadmaps`, `/api/nodes/detail` |
| `/graph` | Interactive dependency DAG (vis-network) | `/api/roadmaps/{id}`, `/api/nodes/detail` |
| `/schedule` | 48-slot day grid, free windows, events | `/api/schedule`, `/api/schedule/availability`, `PATCH/DELETE /api/schedule/{id}` |
| `/jobs` | Pipeline board, stage moves, JD attach, timeline | `/api/applications*`, `/api/jobs*` |
| `/profile` | Editable profile facts + raw profile | `/api/profile`, `/api/profile/facts`, `PATCH/DELETE /api/profile/{key}` |
| `/memory` | Memory DNA — inspect, confirm, correct, forget | `/api/memories*` |

## Architecture visualizer (`/flow`)

Shows the whole mentor architecture statically, then **replays** which nodes and
tools activated for a message:

- **Static shape** comes from `GET /api/architecture` (served from
  `orchestrator/architecture_graph.py`) — the orchestrator graph, the specialist
  agents, and every tool grouped by domain.
- **Live truth** comes from the `trace` field that `POST /api/chat` now returns:
  ordered `{kind: "node"|"tool", ...}` events with latency and decision detail.
- On Run, the page animates through the trace (~320ms/step): visited nodes get
  accent borders, the current node glows, traversed edges animate, used tools
  light up in the rail, and the reasoner's decision (action, toolkit, depth)
  is shown as chips.
- A **Message flow** inspector shows the exact INPUT and OUTPUT payloads for the
  inspected step (the replay's current step, or whatever you click). Click a
  node on the canvas or a row in the trace list to pin it. Tool steps show their
  arguments and return value.

Only **data** is shown — user messages, the assembled context, decisions,
tool args/results, agent outputs. Prompt templates / system prompts are never
included, so the mentor's behavior can't be reverse-engineered from this view.

The trace is produced by wrapping every graph node (`_traced_node` in
`orchestrator/orchestrator.py`) and by the tool-call capture in
`orchestrator/harness.py`. It's fail-open and adds no measurable latency.

## Getting started

1. Start the backend (from the repo root):
   ```bash
   uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
   ```

2. Start the front-end:
   ```bash
   cd web
   npm install
   npm run dev
   ```
   Open http://localhost:3000

The front-end assumes FastAPI is at `http://localhost:8000`. To point at another
host, copy `.env.local.example` → `.env.local` and set `AI_MENTOR_API`.

## Scripts

| Command | What it does |
|---------|--------------|
| `npm run dev` | Dev server with hot reload (port 3000) |
| `npm run build` | Production build |
| `npm start` | Serve the production build |
| `npm run typecheck` | `tsc --noEmit` |

## Project layout

```
web/
├── next.config.ts          # /api/* rewrite proxy → FastAPI
├── src/
│   ├── app/                # App Router pages (one folder per route)
│   ├── components/         # Sidebar, AppShell, Drawer, Markdown, ui primitives
│   ├── lib/                # api.ts (typed client), types.ts, utils.ts
│   └── types/              # ambient declarations (vis-network)
```
