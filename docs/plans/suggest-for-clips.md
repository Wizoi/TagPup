# Suggest for clips: the owner can Suggest and Apply All tags on a folder's clips, as on its photos
Live count: photo_index, counted read-only 2026-11-13: 2,910 clips, every one with a frame-0 vector in `embeddings`, 2,909
with frame rows (11,632 `embedding_frames` rows); 2,243 tagged, 665 untagged (the clips that gain something); 0 faces and
0 `faces_detected` rows on a clip; 0 of the 111 `suggestions` rows are a clip's; 748 folders hold clips, 724 of them photos
too, 24 only clips. kr-track 5 clips, renton_parkrun 0.
Domains: Views (Suggest, the suggester, the page's Suggest), Media (writing a clip's tags, which already exists); integration
reviews the seams. Built as ONE branch by a Views worker; the Media owner reviews the write scenario.

Reverses DECISIONS 2026-11-08 "Suggest looks at photos only" (owner, 2026-11-13: "it should reuse most of what is already
collected"). New fact: the writer no longer refuses clips (`CLIP_PATCH_IN_PLACE` was deleted 2026-11-07), so Apply All on a
clip is a write that exists and has a scenario (C2, `test_a_selection_of_a_photo_and_a_clip_tagged_writes_both`).

| Mechanism | The ONE place it lives | Its owner | New or existing |
|---|---|---|---|
| Who is a Suggest subject | the folder scan `_folder_photos` (`tagpup/web/tagpup_routes.py`): the route's `media == "photo"` test is deleted | Views | existing, rule removed |
| A clip's query vector | `PhotoEmbeddings.of` (frame 0, as V17 measures); matched on best frame by `PhotoIndex.search` | Views | existing: reuse, frame 0 only |
| No face step for a kind the index does not look at | the suggester's face step guarded by `store.photos.FACE_KINDS` | Views (People's rule, read not copied) | one line |
| The neighbour cut 0.35 | one constant `suggesting.NEIGHBOUR_CUT`, read by the suggester and `scripts/measure_pipeline_holdouts.py` | Views | new constant, ends three copies |
| Write a clip's tags | `tagging.apply_suggestions` -> `file_changes.write_fields` -> in-place clip patch | Media / Files | existing: reuse, nothing built |
| Page: count the selection, no photos-only filter or alert | `web/tagpup/suggestions.js` | Views | deletions; the confirm says photos and videos with the helper Smart Rename's confirm uses |

Tags only: the suggester gives a clip no people (the face step is skipped) and, by default, keeps the title wand offered
(a tag-derived caption; the owner says if not). A clip in a folder the library does not hold ("Just look") stays out of Apply
All, because the file-only writer refuses clips: that branch has a live instance (C2
`test_a_write_of_a_folder_the_library_does_not_hold_refuses_a_clip`). A clip Apply All cannot patch is a per-path failure the
server already reports; the page adds no filter back.

Steady-state writer: nothing runs over clips by itself; Suggest stays per folder, on click. A clip added later has its
vector from the indexer as today, and is a subject the next time its folder is Suggested.
One-offs: none.
Cuts: one merge of about 350-450 lines, brief for one Views worker:
1. Delete the route's media test, the page's filter, alert and `isVideo` import; confirm text counts photos and videos.
2. Guard the face step by `FACE_KINDS` (a clip would otherwise take the GPU turn and log an error per clip: 2,910 on a
   whole-library pass; `low`, fixed here).
3. `suggesting.NEIGHBOUR_CUT`; the clip cut stays 0.35 until the owner sets it from the V17 measurement (DECISIONS 2026-11-07).
4. Tests as scenarios: grow V3 (`tests/test_suggest_then_apply_all.py`): a held folder of photos and a clip, plus a clip with
   no vector, Suggest then Apply All then Undo, the clip's row by id, `people == []`, no face model touched, a looking run keeps
   no vector; flip `tests/test_folder_scan_follows_the_library.py:125-136` and `tests/frontend/folder-view-clips.test.mjs:155-175`
   to the new behaviour (each fails on the old code). No new test file.
5. Docs: a DECISIONS row reversing 2026-11-08, `docs/domains/views.md` row 48, `docs/domains/people.md:108`, SPEC line 336.
Performance claim, if made: the Suggest click in a folder of N photos and M clips, timed in a sandbox browser, baseline first.
Open for the owner (defaults stand until said otherwise): frame-0-only query; keep the 0.35 cut for clips; the title wand
stays; the button's "Start Photo Analysis" wording and "photo files" -> "files" in the confirm.
Later, separate: people on clips (face detection on a clip's frames), and a clip rotate action (a display matrix write, Media).
