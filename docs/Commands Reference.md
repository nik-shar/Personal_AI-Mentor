---
created: 2026-08-22
tags:
  - commands
  - reference
---

# ⚙️ Commands Reference

> **Role:** CLI commands, test suites, and scripts reference.

---

## Quick Reference

```bash
# 📡 Web Dashboard
uv run python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

# 💬 Terminal Chat
uv run python -m orchestrator

# ⏰ Scheduler (weekly consolidation)
uv run python -m scheduler
```

---

## Web UI

| Command | Description |
|---------|-------------|
| `uv run python -m uvicorn api.main:app` | Start FastAPI server (port 8000) |
| `--host 0.0.0.0 --port 8000` | Full options for network access |

## Terminal Chat

| Command | Description |
|---------|-------------|
| `uv run python -m orchestrator` | Interactive CLI chat loop |

## Test Suites

### Agent Tests

| Command | Description |
|---------|-------------|
| `uv run python scripts/tests/test_goal_decomposer_quality.py` | Goal Decomposer quality + parallel node expansion |
| `uv run python scripts/tests/test_job_hunter.py` | Full Job Hunter verification (40+ checks) |
| `uv run python scripts/tests/test_orchestrator_harness.py` | Multi-agent pipeline + 8h budget capping |
| `uv run python scripts/tests/test_conversation_transcript.py` | Session transcript persistence |

### DNA Memory Tests

| Command | Description |
|---------|-------------|
| `uv run python scripts/tests/test_dna_memory_store.py` | Phase 1: CRUD, retrieve, confirmation, revision, decay |
| `uv run python scripts/tests/test_dna_reflection.py` | Phase 2: async reflection pipeline |
| `uv run python scripts/tests/test_dna_context.py` | Phase 3: context document + summarize_node wiring |
| `uv run python scripts/tools/eval_dna_memory.py` | Golden-set eval — run after prompt/weight changes |
| `uv run python scripts/tests/test_dna_observations.py` | Phase 4: data-derived observation derivation |

### Cognitive Tests

| Command | Description |
|---------|-------------|
| `uv run python scripts/tests/test_guidelines.py` | Mentor constitution loader + wiring |
| `uv run python scripts/tests/test_onboarding_discovery.py` | Discovery mode + emotional checkpoint |
| `uv run python scripts/tests/test_first_contact.py` | Cold-start emotional intelligence |

### Integration Tests

| Command | Description |
|---------|-------------|
| `uv run python scripts/tests/test_messenger.py` | Slack/Telegram notification |
| `uv run python scripts/tests/test_job_search.py` | Job search API integration |
| `uv run python scripts/tests/test_obsidian_daily.py` | Obsidian daily note append |
| `uv run python scripts/test_schedule_system.py` | Schedule CRUD operations |

## Migration Scripts

| Command | Description |
|---------|-------------|
| `uv run python scripts/migrations/migrate_drop_mentor_state.py --dry-run` | Preview legacy table drops |
| `uv run python scripts/migrations/migrate_drop_mentor_state.py --apply` | Execute legacy table drops |
| `uv run python scripts/migrations/migrate_prompt_keys_to_dna.py --dry-run` | Preview DNA migration |
| `uv run python scripts/migrations/migrate_prompt_keys_to_dna.py --apply` | Execute DNA migration |
| `uv run python scripts/migrations/migrate_vault_to_folders.py` | Dry-run vault folder restructuring |
| `uv run python scripts/migrations/migrate_vault_to_folders.py --apply` | Apply vault folder restructuring |
| `uv run python scripts/migrations/migrate_resume_to_vault.py` | Seed master resume from PDF |

## Environment Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `DNA_REFLECTION_ENABLED=1` | `1` | Enable async memory reflection |
| `MENTOR_DEBUG=1` | `0` | Print per-turn trace to terminal |

## Database

```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Key env vars
DATABASE_URL=postgresql://nik:nik@localhost:5432/ai_companion
OBSIDIAN_VAULT_PATH=/home/nik/Documents/AI-Mentor
```


---

> **Category:** ⚙️ Infrastructure · **Parent:** [[Quickstart Guide]]
