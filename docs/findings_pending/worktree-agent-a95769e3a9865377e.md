# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-10 | low: an index run that finds nothing to index still takes 6 to 8 s after the vector and JSON reads are gone (sandbox copy of photo_index, 0 photos to index), most of it the `from tagpup.ml.clip import output_dim` inside `stored_mismatch`, which imports open_clip and torch (8.3 s of a 12.5 s profiled run under load) to compare a vector length when the library holds vectors. It could be skipped when nothing is to be indexed only by moving the check after the scan, which changes when a model change clears the vectors (the stamps read has_embedding), so it is left for a decision. | open: low |
| B-2 | 2026-10-10 | low: `store.photos` has two `stamps`: the sync's (id, path, mtime, size) and, new, `stamps_with_vectors(conn, model)` (path, mtime, size, holds a vector) for the indexer; a first version named the second `stamps` and silently shadowed the first (found when the test read an empty list). Could be one function with the vector flag as an argument. | open: low |
| B-3 | 2026-10-10 | low: the reload at the end of an index run that indexed (`build_or_update(reload=True)`) has no test of its own: a test would have to run the whole indexer past the model load, which the suite never does; the change to `reload=False` rests on reading that nothing after it uses `photo_index.index` (the sandbox 1-photo run completed with it). | open: low |
