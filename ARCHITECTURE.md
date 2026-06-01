# System Architecture: The All Time Helper (Agentic Ecosystem)

## 🎯 Design Philosophy
"The All Time Helper" is an **Agentic Swarm** designed with a **Human-Centric & Emotional Intelligence (EQ)** wrapper. The goal is to provide enterprise-grade technical support and personal mystical guidance within a single unified interface.

---

## 🏗️ Core Components

### 1. The Swarm Controller (supervisor)
- **Engine**: CrewAI Sequential Process.
- **Identity**: A "Consoling & Supportive" persona that ensures every response—whether code or astrology—is delivered with empathy.
- **Responsibility**: Delegating user prompts to the most qualified specialist.
- **Routing**: `_detect_intent()` classifies complexity as `swarm` (full hierarchy), `single` (generalist only), or `direct` (no tools needed).

### 2. Specialist Agents
| Agent | Domain | Primary Tools |
| :--- | :--- | :--- |
| **Senior Developer** | Code, Bugs, Logic | DDG Search, Code Analysis |
| **Personal Secretary** | Comms, Schedules | SMTP Email (Hybrid Mode) |
| **The Mystic** | Fate, EQ, Symbols | Palm Scanner, Horoscope, Pollinations Image Gen |
| **Generalist** | Single-tool tasks | All tools (fast path, no hierarchy) |

### 3. Vision Integration (High-Fidelity)
- **Pathway**: Local Moondream (Ollama) → Description → Specialist Agent.
- **Use Case**: Analyzing palm photos for the Mystic specialist without sending raw image data to external cloud LLMs (privacy-first).

---

## 🛡️ Professional Safety Standards

### Hybrid Email Mode
- **SIMULATE (Default)**: All email tool-calls are intercepted and written to `simulated_emails.log`.
- **LIVE**: Triggers actual SMTP transmission only when `EMAIL_MODE=LIVE` is verified in the environment.

### Inference Queue (Concurrency Control)
- **Engine**: Custom `InferenceQueue` class in `app/inference_queue.py`.
- **Workers**: 1 async worker by default with configurable backpressure for weak local GPUs.
- **Timeout**: 180s per job with graceful cancellation via `abort_event`.
- **Replaces**: Raw `asyncio.to_thread` which had no backpressure or timeout.

---

## 🧠 Neural Memory (RAG)
The system uses a **Local Persistent Neural Memory** powered by ChromaDB.

- **Indexing Strategy**: AST-aware chunking (Python `ast` module, JS regex extraction) ensures function/class-level semantic boundaries. Replaces the old flat 1500-char slicer.
- **Chunkers**: `chunk_code_python()` (AST), `chunk_code_js()` (regex), `chunk_text_smart()` (paragraph-based fallback).
- **Retrieval Engine**: Local Sentence Transformers for embeddings — 100% private and offline.
- **Rebuild**: `scripts/rebuild_memory.py` re-indexes the entire project with AST-aware chunking.

---

## 🖥️ Frontend Architecture (ES6 Modular)
The frontend was refactored from a 1133-line monolith (`main_v3.js`) into focused ES6 modules:

```
static/js/
├── app.js       ← Entry point & orchestrator (imports all modules, window.* bridge)
├── state.js     ← Reactive AppState class with subscribe()/set() mechanism
├── api.js       ← All fetch/networking (auth, chat streaming, cloud sync)
├── ui.js        ← All DOM manipulation (addMsg, renderHist, modals, theme)
├── mascot.js    ← Cursor tracking, jiggle/pop animations, bot reactions
├── utils.js     ← renderMarkdown, copyCode, downloadCode (global, non-module)
├── palette.js   ← Command palette Ctrl+K (global, non-module)
└── particles.js ← Canvas particle system (global, non-module)
```

### Module Dependency Graph
```
state.js (no deps) ← api.js, ui.js, mascot.js ← app.js (orchestrator)
```

### Key Design Decisions
- **window.* Bridge**: `app.js` exports critical functions to `window` for HTML inline `onclick` handlers.
- **Reactive State**: `state.subscribe(key, callback)` enables decoupled UI updates.
- **Legacy Compat**: `utils.js`, `palette.js`, `particles.js` remain as non-module `<script>` tags because they set `window.*` globals.
- **Rollback**: `main_v3.js` is retained on disk (not loaded) as emergency rollback.

---

## 📈 Observability & Logging
- **Action Logs**: Structured capture of `[Agent → Selection → Tool → Output]`.
- **Inference Queue Metrics**: Job queue depth, timeouts, and cancellations logged.
- **Simulated Sink**: Audit logs for all "virtual" actions that simulate side-effects.
- **Semantic Sync**: `rebuild_memory.py` ensures the Vector DB matches the physical codebase (AST-aware).
