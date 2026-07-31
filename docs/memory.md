# Memory

The project uses two memory layers:

## Neural Memory
- ChromaDB stores semantic memory under `.project_brain/` by default.
- `query_memory()` fails closed and returns empty context on Chroma errors, so chat execution continues.
- Collection reads, writes, pruning, warmup, and health probes share a process-level re-entrant lock.
- A query failure opens a short retry circuit instead of disabling retrieval for the rest of the process. `/admin/status` reads that same circuit and reports a bounded retry delay without exposing raw storage errors.
- Stale and overflow pruning preserves entries marked `permanent`; overflow removal uses stored timestamps rather than arbitrary collection order.

## Source-of-Truth Docs
- Prefer markdown docs and repo search before reaching for semantic memory.
- Use these docs first for architecture, routing, and pipeline context.
- Keep memory as a recovery and recall layer, not the primary source of truth for normal coding work.
- .project_brain/ is generated runtime state containing the Chroma database and index files. It is ignored by Git and must be recreated locally when absent; local contents are never deleted as part of repository maintenance.
