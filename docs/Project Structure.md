---
created: 2026-08-22
tags:
  - project
  - structure
---

# 📁 Project Structure

> **Role:** Repository layout and file responsibilities.

---

## Directory Tree

```
ai/
├── agents/                          # Specialist stateless sub-agents
│   ├── Goal_Decomposer/             # Learning roadmap DAG + Obsidian writer
│   │   ├── goal_decomposer.py       #   Entry point, 2-phase generation, graph
│   │   ├── state.py                 #   4-bucket content schemas, state
│   │   ├── skills.md
│   │   └── __init__.py
│   ├── Job_Hunter/                  # JD-tailored resumes + pipeline tracker
│   │   ├── job_hunter.py            #   6-branch graph, nodes, prompts
│   │   ├── state.py                 #   MasterResume, TailoredResumeDraft, state
│   │   ├── render.py                #   View builder, metric guard, LaTeX render
│   │   ├── resume_template.tex      #   Fixed LaTeX template
│   │   ├── architecture.md          #   Agent architecture doc
│   │   ├── skills.md
│   │   └── __init__.py
│   └── Linkedin_writer/             # RAG draft writer + critic verifier
│       ├── linkedin_writer.py       #   Entry point, graph, 5 nodes
│       ├── State.py                 #   LinkedInWriterState
│       ├── RAG.py                   #   Chroma vectorstore loader
│       ├── approve.py               #   Human approval stub
│       ├── architecture.md          #   Agent architecture doc
│       ├── skills.md
│       └── __init__.py
│
├── api/
│   └── main.py                      # FastAPI server
│
├── web/                             # Next.js dashboard (replaced the legacy ui/)
├── mentor/                          # PI agent core: extensions + generated skills
│
├── orchestrator/                    # The mentor's reasoning core
│   ├── orchestrator.py              # StateGraph assembly, nodes, entry point
│   ├── state.py                     # OrchestratorState TypedDict
│   ├── runner.py                    # OrchestratorRunner wrapper
│   ├── registry.py                  # Agent registry (AgentSpec)
│   ├── config.py                    # DB URLs, routing hints, profile keys
│   ├── llm.py                       # 3-tier LLM factory
│   ├── tracing.py                   # LangSmith + local spans
│   ├── __main__.py                  # CLI chat loop
│   ├── architecture.md
│   ├── skills.md
│   │
│   ├── nodes/                       # Pure helpers; the LangGraph nodes live in orchestrator.py
│   │   ├── reasoner.py              #   LLM reasoning core → ReasoningDecision
│   │   ├── context_builder.py       #   AgentTask assembly (MemorySlice per agent)
│   │   ├── memory_merger.py         #   Memory delta persistence
│   │   └── synthesizer.py           #   Mentor voice framing
│   │
│   ├── memory/                      # Persistent memory services
│   │   ├── __init__.py              #   THE FACADE — purposes, not tables
│   │   ├── core.py                  #   Engine/session/schema/embedding (one owner)
│   │   ├── identity.py              #   P1 identity (who is he, what he wants)
│   │   ├── store.py                 #   MemoryManager (profile + episodic)
│   │   ├── models.py                #   SQLAlchemy ORM models
│   │   ├── dna_store.py             #   DNAMemoryStore (organic memory)
│   │   ├── dna_reflection.py        #   Async memory reflection
│   │   ├── dna_context.py           #   Context document builder
│   │   ├── roadmap.py               #   Curriculum store (roadmap.yaml) + notes
│   │   ├── retriever.py             #   Semantic search
│   │   ├── consolidator.py          #   3-stage memory aging
│   │   ├── daily_summary.py         #   End-of-day narrative memory
│   │   ├── grid.py                  #   48-slot day-grid math (pure)
│   │   └── topic_graph.py           #   DAG operations (networkx)
│   │
│   └── cognition/                   # Persona, onboarding, observations
│       ├── persona.py               #   Voice rules + examples
│       ├── guidelines.py            #   Constitution loader
│       ├── personality.py           #   Cold-start defaults
│       ├── onboarding.py            #   Discovery mode
│       ├── observations.py          #   Pattern detection
│       └── metrics.py               #   Momentum math
│
├── schemas/                         # Pydantic contract models
│   ├── agent_io.py                  # AgentTask, AgentResult
│   └── memory.py                    # DNAMemory, MemorySlice
│
├── integrations/                    # External integrations
│   ├── search.py                    # Web search (Tavily + DuckDuckGo)
│   ├── job_search.py                # SerpApi + JSearch + ATS
│   ├── messenger.py                 # Slack/Telegram notifications
│   └── obsidian_daily.py            # Daily note append
│
├── scheduler/                       # Background scheduling
│   ├── engine.py                    # APScheduler engine
│   ├── consolidation_job.py         # Weekly memory consolidation
│   ├── runner.py                    # CLI entry
│   └── __main__.py
│
├── scripts/                         # 40+ test/migration/benchmark scripts
├── docs/                            # Obsidian vault docs
│   └── Home.md                      # Vault index
├── data/                            # SQLite checkpoints, linkedin posts
├── chroma_store/                    # ChromaDB persistent storage
├── mentor_agent_guidelines.md       # Operating constitution
├── dna_memory_redesign_v2.md        # DNA memory design doc
├── CLAUDE.md                        # Project context for AI assistants
├── COMMANDS.md                      # CLI reference
└── README.md                        # Top-level README
```


---

> **Category:** 📁 Project · **Parent:** [[Home]]
