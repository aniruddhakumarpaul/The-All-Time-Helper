# Memory

The project uses two memory layers:

## Neural Memory
- ChromaDB stores semantic memory under `.project_brain/` by default.
- `query_memory()` must fail closed and return empty context on Chroma errors.
- Memory operations are lock-guarded.
- `repair_memory_store(preserve=True)` can rebuild the store from available exports.

## Source-of-Truth Docs
- Prefer markdown docs and repo search before reaching for semantic memory.
- Use these docs first for architecture, routing, and pipeline context.
- Keep memory as a recovery and recall layer, not the primary source of truth for normal coding work.

