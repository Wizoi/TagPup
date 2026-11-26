# Working on a new folder: a folder added to a library (or a new library) can be worked on at once, and every page says what is still under way
Live count (2026-11-26, read-only): rows of a folder gone with its root, kept "missing": photo_index 1, kr-track 0, mu_dance 0,
renton_parkrun 0. Everything else is a user action, through the real writers: create a library and add folders (mu_dance,
5 folders, 2026-11-21 to 26), add a folder from Organize, open Library while it indexes, save while the index bakes.
Domains: Views (the pages), Files (the write precondition), People (the index's new-faces sentence); integration reviews the
seams. Source: the user-flow review of 2026-11-26 (findings #3407-#3409); owner answers in DECISIONS 2026-11-26.

| Mechanism | The ONE place it lives (module, function) | Its owner | New or existing |
|---|---|---|---|
| Which folders are queued or indexing, and their order | `GET /api/folder/index-active` (`tagpup/web/folder_routes.py`) | Integration | existing: TagPup reads it as TagTuner does (`web/tuner/indexing.js`) |
| TagPup following an index (bar, its message, the end) | `web/tagpup/suggestions.js` `checkIndexingStatus` | Views | existing: made to resume on reopen and to say its end |
| What an index's end refreshes | one hook `upper.indexEnded(folder)`: navigator counts (`navigatorCountsChanged`), the open folder (`scanFolder(true)`), the library view (`refreshLibraryView`) | Views | new hook, existing refreshers |
| A folder's cached scan being out of date | `web/tagpup/folder.js` cache: not shown when the folder's index ended after it was taken, or is under way | Views | existing cache, one more condition |
| A save refused as changed on disk | `web/common/photo-write.js` `postOrSendAgain` (both pages, every save naming a base; moved from edits.js, #3415): re-read, resend once with the edit's `before` as its base, the server comparing it (`tagging.py` `_differs_from_base`, #3414); else today's sentence (`photoChangedOnDisk`) | Views (page), Files reviews (precondition unchanged on the server) | existing, changed meaning (DECISIONS 2026-11-26) |
| Whether leaving is safe | `web/tagpup/write-queue.js` / `edits.js` status: "sent: the server finishes it" vs "waiting in this page"; `pending.js` leave prompt offers Keep writing | Views | existing, words and one choice |
| Why Suggest found little | `tagpup/services/suggester.py` sentence, given by the saved run too (`tagpup/jobs/suggestions.py`) | Views | existing sentence, one more reader |
| An index's new-faces sentence | `tagpup/services/indexing.py` (its message), shown on the page that follows the index | People (sentence), Views (page) | existing, surfaced |
| A missing photo's explanation and its remedy | `web/tagpup/stale.js` missing line, linking Manage folders' Remove (`web/common/folder-manager.js`) | Views, Files reviews the words | existing, words and a link |
| Folders the library holds but has not indexed yet in the Folders tab | the navigator's folder section (`tagpup/services/library_view.py`) adds the library's queued folders (`store/folders`, the index queue), marked queued / indexing, count 0 until indexed | Views, Files reviews | existing list, new kind of entry (owner, 2026-11-26) |
| A new library's first step | `web/common/library.js` create: says "library", opens Manage folders | Integration | existing |

Steady-state writer: nothing here writes a photo, a row or a file of its own; every write stays the existing one (the index,
`file_changes.write_fields`, Manage folders' Remove). The resend is the same write, sent once more with the re-read stamp.
One-offs: none.

Not changed (owner, 2026-11-26): a folder gone with its root keeps its rows until the owner removes it (DECISIONS 2026-10-28
stands); Smart Rename does not wait for the folder's index.

Cuts (each one worker, one domain, under the budget; A first, then the rest, B and E in parallel with A):
- **A, Views: TagPup follows an index to its end** (#3407, #3408). "Indexing N of M folders" from index-active; the bar resumes
  when a folder opens (after a library view too); a cache taken before the index ended is not shown; `indexEnded` refreshes the
  navigator, the open folder and the library view; the index's own message (the bake, the new-faces sentence) stays on the
  status line instead of "Ready". Files: suggestions.js, folder.js, library-view.js, navigator.js, gear.js, membership.js, hooks.
- **B, Views (Files reviews): a refused save is re-read and resent** once when the fields it touches did not change; otherwise
  today's sentence. A second refusal is not resent. Files: edits.js and its tests.
- **C, Views: what is safe to leave** (after B): the write line says sent vs waiting; leaving a folder with changes waiting
  offers Keep writing (they finish as a library view's do); the browser's Leave prompt only while something waits in the page.
- **D, Views + People: Suggest says what the library lacks** (#3409) in a saved run; the new-faces sentence links Re-examine
  (after A).
- **E, Views: a missing photo says why it stayed** (its folder is gone; a full check keeps rows whose folder is gone) and links
  Manage folders' Remove.
- **F, Views (Files reviews): queued folders in the Folders tab** (after A), marked, count filling as indexed.
- **G, Integration: a new library opens Manage folders**, and its prompt says library, not database.
