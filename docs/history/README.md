# History

Design logs and closed findings. **Read only when asked, or to learn why something is as it is.**
Nothing here is current by itself: a number, a date, a branch name or a "not built yet" in these files is as it
was on the day it was written. The current design is [../ARCHITECTURE.md](../ARCHITECTURE.md) (a map),
[../INVARIANTS.md](../INVARIANTS.md) (the rules) and [../DECISIONS.md](../DECISIONS.md) (the owner's decisions).

## The former ARCHITECTURE.md

On 2026-10-09 (a feature freeze; trunk 17afc1b) `docs/ARCHITECTURE.md` was 504 KB, more than a session can read.
It became a map, and every section it no longer holds moved here **verbatim**, one file per theme. The map's last
section lists each former heading and the file it is in, so a comment that says "docs/ARCHITECTURE.md, phase 9d-1"
or "Roots and machines" still resolves.

| File | Holds |
|---|---|
| [ARCHITECTURE_phases.md](ARCHITECTURE_phases.md) | The original Runtime, "Where everything goes" and Data model text; phases 1 to 7 (foundations, services, store, data model, one owner per rule, one server, models in the package, pages, no shims, MCP); phase 10; the Decisions and Progress tables as they stood |
| [ARCHITECTURE_jobs_sync_journal.md](ARCHITECTURE_jobs_sync_journal.md) | Phase 7.5 (the journal, migrations, file changes), 7.6 (settings in the library), 8 (sync, snapshots, recurring jobs, the watcher, always-on, self-update), 8.5 (Activity), the file access check |
| [ARCHITECTURE_roots_and_markers.md](ARCHITECTURE_roots_and_markers.md) | Roots and machines (the root-relative stored path, adoption, Verify, relocation) and folder ids (the `.tagpup` marker) |
| [ARCHITECTURE_library_views_and_search.md](ARCHITECTURE_library_views_and_search.md) | Phase 9: derived tables, thumbnails, the windowed grid, the navigator, bulk edits by id, the owner's reviews, search, camera and lens words, the searchable-fields backlog |
| [ARCHITECTURE_identity.md](ARCHITECTURE_identity.md) | Identity by id (stage 1) and People by id, stage 2: a person is a tag-tree node's id |
| [ARCHITECTURE_faces_and_naming.md](ARCHITECTURE_faces_and_naming.md) | Faces on the photo, Name faces from tags, the face-region backlog, the owner's decisions for the AI pipeline |
