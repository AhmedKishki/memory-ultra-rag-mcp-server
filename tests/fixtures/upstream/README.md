# Upstream format fixtures

These two files are **not written by this package**. They are the exact bytes
UltraRAG's own memory server produced, captured on 2026-09-22 by running
`servers/memory/src/memory.py` from the pinned revision
`3a709a2aea3fbe46acca59c422621c94b6e86857` (declared version `0.3.0.2`) with
`ULTRARAG_UI_STORAGE_ROOT` pointing at a temporary directory, then calling

```python
get_global_memory("fixture")
save_memory(
    "fixture", ["What do you remember about me?"], ["Only what you asked me to keep."]
)
```

| File | What upstream wrote it with |
| --- | --- |
| `standing_memory.md` | the template `get_global_memory` creates on the first read of a user |
| `daily_round.md` | one round appended by `save_memory`, including its header line and timestamp |

`tests/test_fidelity.py` compares what this package writes against these files, so
a change to the format fails a test instead of drifting quietly. The timestamp in
`daily_round.md` is normalized in the comparison, because a round is stamped when
it is written.

To recapture them after an upstream revision change, run the module from the new
checkout the same way and replace both files; the revision recorded in
`src/memory_ultra_rag_mcp/reference.py` moves with them.
