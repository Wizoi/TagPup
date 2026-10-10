# Decisions

The owner's decisions, dated, one line each; and the design decisions that shaped the code. **Not reopened
without a new fact.** Collected 2026-10-09 from the former `ARCHITECTURE.md` (its Decisions table and its
sections' *(owner, date)* marks, now in [history/](history/README.md)), from [findings.md](findings.md) and
its [archives](history/README.md), and from CLAUDE.md. The longer reasoning is in the findings row or the
history section named. The rules these produce are in [INVARIANTS.md](INVARIANTS.md).

## Architecture and process

| Date | Decision |
|---|---|
| 2026-09-23 | One owner per concern, each with a guard test; three kinds of data (file copies, decisions, derived) with one writer each; a photo has an integer id and its path is an attribute. |
| 2026-09-23 | The web layer is Flask and Waitress. One server process serves both apps, on the ports they used (8090 TagPup, 8080 TagTuner). |
| 2026-09-23 | `Library` and `Result` live in `core` (plain values every layer passes along). |
| 2026-09-24 | One composition root, `tagpup.runtime`; the layers stay as they are (letting `ml` read `config`, merging the infrastructure layers, ports-and-adapters throughout were weighed and rejected). A service that uses a model is given it. |
| 2026-09-24 | A service that reads returns what it read and raises `NotFound` (404) or `Refused` (400); opening a photo, Explorer and the folder dialog stay web routes. |
| 2026-09-24 | Five maintenance scripts retired; every index entry whose file was gone dropped; a new library is seeded with one face root, People, which cannot be taken away. |
| 2026-09-24 | The two servers are two Flask apps from one factory, one per port; the sockets are bound with `SO_EXCLUSIVEADDRUSE`. |
| 2026-09-25 | The pages are ES modules (`web/tagpup/`, `web/tuner/`, `web/common/`) with no build step; page tests load modules into jsdom in import order, each in a scope of its own. |
| 2026-09-25 | The MCP server names a library in every call; it has no library of its own (#100); its reads are `services.inspect`. |
| 2026-09-25 | Bulk edits and migrations are recorded in a journal, per changed column, applied and undone only where rows are what the change expects, rehearsed by a rolled-back dry run. Full backups only for destructive migrations. SQLite's session extension was weighed and its semantics copied, not the library. |
| 2026-09-25 | A library's settings live in the library, changed from TagTuner's gear through the journal; `config.ini` is retired; the data folder is fixed (`TAGPUP_HOME/data`). Settings with consequences are locked behind a Change... that asks for each consequence. From 2026-10-09 nothing reads `config.ini` (a library holding no settings gets the defaults; an old file is ignored and left on disk). |
| 2026-09-25 | Input rules have one owner, `tagpup.core.validation`; output is escaped by building elements from text. |
| 2026-09-25 | Each library keeps three daily, one weekly and one monthly snapshot; it is a read transaction and holds no writer back (amended 2026-09-26). |
| 2026-09-25 | Recurring operations are registered in `tagpup.jobs.recurring` and run by one runner in the web process; no Windows Task Scheduler. The CLI runs one by hand; the MCP server runs none (#334, 2026-09-26). |
| 2026-09-25 | The server and jobs run as one always-on process started at login from the Startup folder, not as a Windows service. Idle models are released. |
| 2026-09-26 | Event-driven first: work that follows an event runs at once or is queued; a schedule is only for safety, retention and catch-up. |
| 2026-09-26 | The server answers this PC only (127.0.0.1 and ::1) until phase 10 adds logins; `--listen lan` is for then. |
| 2026-09-26 | Each library has root folders and ignored folders (settings, TagTuner's gear); the idle registry releases the photo indexes too (#345, #362). |
| 2026-09-28 | The retro: a worker's brief names how the work fails (interrupted, two at once, a read that fails, the real data, what the owner sees); TagPup shows its write queue bottom-right (#387, #406). |
| 2026-10-02 | The stored path is root-relative (`@root/relative`), not a relocate alternative; **nothing converts a library unasked** (`roots adopt` is explicit). |
| 2026-10-02 | A journaled change stays undoable across an ADDITIVE migration that touches no table a change can name; any other migration since blocks the undo. |
| 2026-10-02 | Just-look reverses the 2026-09-28 rule that a refused write refuses the request (#527). Automatch is not journaled; no journal polish until the major changes are done (#639). |
| 2026-10-04 | Stabilize before new features: review every open finding and fix small issues first. An update must hand over without killing (#726). |
| 2026-10-07 | Sandboxes that load models share the owner's card lock; a server the install starts has no window (#799, #801). |
| 2026-10-09 | **A feature freeze.** Allowed: deletion, simplification, docs, test speed, bug fixes for what the owner hit, installing what is merged. A branch is about 1,200 lines, one concern; at most two review rounds; severity `data` / `wrong` / `low`, only the first two block a merge; one migration in flight; a new feature states its live count (under about 20, a one-off script); each branch removes as many concepts as it adds. |

## People, names and tags

| Date | Decision |
|---|---|
| 2026-09-23 | A person is a leaf tag; the tag is a path (`People/Name`), the identity the leaf. Six faces both excluded and named kept their names (#3). |
| 2026-10-02 | Identity by id: faces and `photo_people` carry the tag node's id; `name` is a cache of it. |
| 2026-10-04 | **A branch is never a person**: a group of people (Family/Thackeray) is not tagged as a person, and a tag used on photos gets no children; both enforced as refusals with an explanation (2026-10-04, 2026-10-07; #660). |
| 2026-10-04 | Dates "Other" row = the year's photos with no month (#685); keep counting photos in the People tab for now (#686). |
| 2026-10-07 | The AI pipeline: plumbing changes first; replace the face models (insightface; the non-commercial licence is accepted) keeping the old vectors beside the new; replace CLIP (SigLIP 2) with embeddings versioned by model; search by what is in the picture; bake the CURRENT model's vectors and the face regions (MWG) into the photo's XMP. |
| 2026-10-07 | The sideways-photo (orientation 5-8) redesign is un-tabled: boxes stored as the photo displays; rotation done outside TagPup is detected by a fingerprint; every detected face is written, an excluded one removed; a small subset first, no full dry run. |
| 2026-10-07 | People by id stage 2: the 4 names with no person tag get the tag created; a name matching two tags is confirmed by name before anything is written; deleting a person tag faces use refuses unless forced; old history is translated to stay undoable. |
| 2026-10-09 | Names on a group tag, or with no person tag, are **not unnamed on their own**: they are listed for the owner to decide (names to review), possibly linking the name to a person in another library. Every recommendation of the people-by-id design accepted as written. |
| 2026-10-09 | The backfills `tags-from-faces` and `faces-from-tags` take `--folder` (#994). |

## Folders, roots and markers

| Date | Decision |
|---|---|
| 2026-10-02 | Roots: no cross-process lock between the server's Suggest and a CLI index or sync; rows spelled by the share address while the root is a drive refuse the whole adoption, the dry run naming them (#448). |
| 2026-10-08 | A folder gone from disk whose parent is there is the unit followed; **a ghost (a gone folder with no rows under it) is only reported**, never followed (#919, #925, #934). |
| 2026-10-08 | Folder ids: a hidden `.tagpup` file in each leaf folder holding a library's id and its UUID for the folder; relinking by the marker is exact and automatic, per library; `relink-folders` does not mark a folder it followed; written only by an explicit command. |
| 2026-10-08 | Truncated JPEGs stay refused, not read (#415). |

## Standing rules from the owner's memory of the project

| Date | Decision |
|---|---|
| 2026-09-28 | Date Taken, not file mtime; mtime changes from metadata writes are accepted. |
| 2026-10-02 | Only photo_index and kr-track are active libraries; no journal polish (undo-chain niceties) until the major changes are done. |
| 2026-10-04 | Search is built on SQLite FTS5 after research of comparable managers (#728). |
| 2026-10-04 | Library views: several rows selectable at once, People by branch, one sort (#668-#675, #712-#714). |

## Keeping the project small (2026-10-09 / 2026-10-10)

| Date | Decision |
|---|---|
| 2026-10-09 | A feature freeze, and the rules of CLAUDE.md "Keeping the project small": branches of about 1,200 lines or fewer, at most two review rounds, a severity on every finding (only `data` and `wrong` block), one migration in flight, a new feature states its live count, each branch removes as much as it adds. |
| 2026-10-10 | One-offs are fixed by hand and flagged, not auto-corrected: a case affecting about 5-20 photos gets a check that lists them (counts by default, names behind the reveal rule), not repair code with special conditions and tests. |
| 2026-10-10 | A person's id represents the ENTIRE TAG PATH (the model of Windows Live Photo Gallery). The leaf name is never identity; the shared-leaf case (a pet and a person under different roots) was an early design mistake, fixed by hand. Tagging by typing a name stays: it resolves to a path, and two matches ask "which one?" by path. Supersedes the 2026-10-09 short-label-when-shared design. |
| 2026-10-10 | Folder markers (`.tagpup`) are deleted end to end: sync keeps its name/size/time pairing; a folder renamed and re-saved before the next sync is flagged missing and renamed back by hand. Reverses 2026-10-08. |
| 2026-10-10 | Names to review: the acting UI is replaced by the doctor's list once the owner has settled the names by hand. |
| 2026-10-10 | Camera search table (`search_gear`) and `photo_meta` are dropped with migration 30; a camera search scans the stored metadata. |
| 2026-10-10 | The big photo_index library is archived out of `data/` (snapshot first; kept as a scale-test copy). |
| 2026-10-10 | Machinery with zero live use is deletion-first, each deletion its own small branch, taken in the domain registers' order (untracked reports/One-off register - *.md): `relink-folders`, `reread-fields`, `dedupe-spelled-rows`, `backfill_document_ids`, journal prune, name-only face replay, `legacy_counters`, `faces_pending`. The supervisor class, the Activity file-access probes, the resumable bulk-edit job, roots adoption/Verify, damaged-photo handling and the legacy read-only CLI commands wait for the owner's yes, one at a time. |

To find the rest, search [findings.md](findings.md) and `docs/history/findings_closed_*.md` for `decided` and
`(owner`; a row marked so is a decision, with the commit that built it.
