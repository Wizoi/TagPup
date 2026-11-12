# People and faces: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-people.md`: its Scope, the contract the other domains rely on, its
invariants and guards, and its scenarios P1 to P7 and P10 to P12. This page states each of the seven conditions of health
(docs/OWNERS.md) with its evidence and date. A condition not met is the domain's queue. Counted read-only 2026-11-08
(`db.readonly_uri`; counts only, no name or path), unless a line carries another date.

## What the domain holds (counted 2026-11-08)

| Library | People | Faces | With a person | By hand | Excluded |
|---|---|---|---|---|---|
| photo_index | 408 | 183,669 | 58,457 | 126 | 2 |
| kr-track | 108 | 12,664 | 2,780 | 1,638 | 2 |
| renton_parkrun | 257 | 3,315 | 1,039 | 1 | 0 |

A person here is a node of the tag tree with the faces flag and no child. The groups, the face roots and the listed people
of the 2026-10-15 table are not recounted and are left out. All three libraries are on schema 38, on the one face model.

- A face with a name and no id: 0 / 0 / 0. A face whose `tag_id` names no node: 0 / 0 / 0. A face whose cached name differs
  from its node's leaf: 0 / 0 / 0. A `photo_people` row with no id or a dangling one: 0 / 0 / 0.
- A face or a `photo_people` row on a clip (`photos.kind = 'video'`): 0 / 0 / 0. The index looks for faces in photos only
  (`store.photos.FACE_KINDS`; a clip's faces wait for a migration of their own, DECISIONS 2026-11-02, "THE OWNERS' PLANS"),
  and no bake writes faces into a clip (`BAKED_KINDS`).
- People sharing a leaf: one pair in photo_index, none in the other two (known since 2026-10-15, recounted). Two cousins
  called alike are named by id and a typed bare name that fits both is refused (P6).
- Stored Suggest rows holding a person's name with no `/` (the bare name only `Directory.of_name` finds): 0 of 111 in
  photo_index, 79 of 546 in kr-track, 0 of 1,164 in renton_parkrun (2026-11-08; 98 chips counted on 2026-10-15).

Face vectors (migration 33, 2026-10-18) are in `face_embeddings`, one per face and face model. Every reader of vectors is
given the key it reads under (a required argument): a decision reads the library's `faces.model` once
(`store.faces.model_key`, on its own connection), reads its vectors under it and decides by that key's cuts; a set of
vectors kept between reads carries its key (`clustering.FaceRows.key`, `identify.UnnamedFaces.key`) and is compared only
with vectors of the same key (`FaceRows.under`), so a switch committed meanwhile never sets one model's vectors beside the
other's, nor reads them by the other's cuts. Every face holds a vector under its library's model (183,669 / 12,664 / 3,315
faces, counted 2026-11-08), and no writer files a face without its vector, so every reader of a face's vector joins it
(`store.faces.WITH_VECTOR`).

## The seven conditions (2026-11-08)

1. **No reach-ins.** Met. The uses of People's internals by other domains are rows of the Integration charter's contract
   table: `core/vocabulary` (row 60), `people_vocabulary` / `read_people_vocabulary` (61), `face_refs` / `crop_of` /
   `cache_crop` (62), `taxonomy.seed` (63), each with its guard test. Open and recorded: #2741 (`core/vocabulary.py` holds
   the caption and title readers, Files' concern, in a People module) and #2740 (two Organize page tests are mapped to
   Views though they exercise People's `selection-panel.js` and `face-boxes.js`); both `low`, neither a reach-in into
   People's internals.
2. **No rule written twice.** Met.
   - Who is a person and who a group: `person_ids.People`, alone. Who makes a new person: `face_people.made`, the tag first
     (#1252). The store's face writers refuse a name no node has.
   - The settings a detection is recorded under are held equal to those a face model is made from
     (`tests/test_service_faces.py`, ADetectionIsRecordedUnderTheModelsSettings).
   - The face in the background has one definition, `clustering.is_background(box, size)`: under 2% of the shorter
     side of the picture as the photo's row stores it (DECISIONS 2026-11-12). The photo's strip, Organize, Suggest,
     cluster-faces and Name faces from tags each ask it with the stored size (`store.photos.picture_size`,
     `store.faces.for_clustering`), never the file; a NULL size puts no face in the background (P12; #2738, #2761).
3. **No hidden coupling.** Met. Clustering reads a photo's people as ids in a statement of its own (`store.photos.records(
   people_ids=True)`, #1372, #1373) and no reader takes ids for names. A page and the server share one table for filing a
   person (`person-filing-table`) and one matrix for who a face looks like (P11).
4. **No concept that could collapse.** Met. `person_ids.wire` is gone (#1482): a person travels as the id of their node,
   and only the doors take a typed name (`faces.who_is`, `face_people.made`). `Ref` and `Person` stay two: by design, below.
5. **No branch without an instance.** Met. Kept, each with its user action or count: a typed name (the pages' New Person
   dialog and the face box's typed name; neither the CLI nor the MCP names a face); `Directory.of_name` and `of_reference`'s
   bare-name arm (79 stored kr-track Suggest rows above, #1426); `is_background`'s NULL size, which puts no face in the
   background (0 faces on such a photo, counted 2026-11-12; the user actions that make one: a Suggest look at a photo
   the library has no row for, an undo that puts back a row from before migration 39, a header the indexer could not
   read; DECISIONS 2026-11-12). A person at the top of the
   tag tree is legal (DECISIONS 2026-11-08, "A PERSON AT THE TOP OF THE TAG TREE IS LEGAL"): no branch refuses it and
   none is needed. The eleven people made bare at the top of kr-track in 2026-11 were merged under People on 2026-11-02
   (docs/watching.md #2360, the path that made them not found); none is left at the top of any library (0 nodes with the
   faces flag, no parent and no child, counted 2026-11-08).
6. **Scenario tests, each file owned and mapped.** Met by the owner's bar (DECISIONS 2026-10-15, recommendation 1): P1 to
   P7 and P10 to P12 each have a file in `tests/owners.py`, and `tests/test_the_test_map.py` fails a file with no owner. The
   unit files that stay, and the rule each holds, are listed below.
7. **No open data or wrong finding, and every low fixed or by design.** Met on `tools/add_findings.py --open` of
   2026-11-08: 20 open, all `low`, none `data` or `wrong`. People's own open lows are #2740, #2741 and this page's #2739
   (#2738 and #2761, the background rule, fixed by its one rule on 2026-11-12). #1 and #831, the
   faces of a sideways photo, are closed `decided:` (owner, 2026-10-16). Fixed and by design rows older than this page are
   in docs/history/ and below.

## The scenarios of this domain

P1 to P7 (the id of a node as the person; names, tags and faces in step; Identify; Name faces from tags as a resumable job;
a tree edit; two cousins called alike; ignore, assign, restore and undo) are in `tests/owners.py`. Added or changed since
2026-10-28:

- **P10, a library on its face model** (`tests/test_a_library_indexes_and_suggests_with_its_face_model.py`): a new photo is
  indexed and Suggested by the library's own face model, the face under the floor is not made, and Name faces from tags reads
  vectors by that model's cuts (`clustering.Cuts`).
- **P11, one nearest person everywhere** (`tests/test_a_face_gets_one_nearest_person_everywhere.py`): Suggest, Identify, the
  strip, the boxes and Name faces from tags give a face the same nearest person; a person who changed over the years is
  compared by the faces of the face's own years, an undated face by every year (`FaceRows`, `in_era`).
- **P12, a face in the background is one answer** (`tests/test_a_face_in_the_background_is_one_answer.py`): in a
  library whose sizes are partly filled, a 45 x 45 face on a 2400 x 2300 photo whose row stores its size is hidden by
  the strip and offered or named by none of Organize, Suggest, Name faces from tags and cluster-faces' elimination;
  on a photo whose row stores no size it is shown, offered and named by every one (`clustering.is_background`).
- **The naming queue.** Naming on the photo page is shown at once and written after it: the photo's write queue first, then
  the database, shown as it was if the write fails; faces clicked while the file is being written are written together as
  one write of the file for their people, then each face named (`web/tagpup/face-boxes.js`, `photo.js`;
  `tests/frontend/face-strip.test.mjs`, `organize-faces.test.mjs`). Name faces from tags is the job
  (`tagpup/jobs/naming_faces.py`): `tests/test_naming_faces_job.py` and `tests/test_name_faces_routes.py` under P4, a restart
  in the middle settles and a second click does not repeat.
- **Selection suggestions.** The selection panel's Suggested People also lists who the selected photos' unnamed faces look
  like, asked once for the photos not answered yet (`POST /api/photo-faces/suggestions`, `web/tagpup/selection-faces.js`,
  2026-11-02) and forgotten when one of the photo's faces is named, unnamed or left out; a chip the faces alone offer does
  not promise that Apply All writes it (#2364). The test is `tests/frontend/selection-suggests-who-the-faces-look-like.test.mjs`,
  mapped to Views (#2740); the answer is People's, from the one shared matrix.
- **Clips.** A clip has no face and no `photo_people` row (0 on all three libraries); its tags written by a save or a tree
  rename are the Files domain's (`tests/test_a_tag_tree_edit_of_a_tag_a_clip_holds.py`). Suggest looks at photos only (DECISIONS 2026-11-08, "SUGGEST LOOKS AT PHOTOS ONLY").

## By design (each row closed `decided:`)

- **A run's decided vectors and the library's key (2026-10-18, migration 33)**: naming by tag reads face vectors under
  the library's face model, read once per write (`faces.model_key`), while `derived.Batch.decided` keeps each person's
  decided vectors for the run. A switch of `faces.model` mid-index leaves the run on the face model it began with
  (its vectors, and the cuts it decides them by: the key travels with them; cluster-faces likewise), so the rest of the run's faces have no vector under the new key: each is left
  (`unreadable_face`), or named by its photo's one keyword person where that person has no decided face under the new
  key and no other photo waiting (#839), the tag's own route. Two models' vectors never meet in one product; the next
  index or cluster-faces reads them again.
- **Identify on a library switched to the new face model (2026-10-18; 2026-10-20)**: every reader compares faces under the
  library's model. The faces that held no vector under it (an old face the deleted pass matched with nothing it found,
  1,195 of kr-track's, counted 2026-10-20) were deleted by `retire-faces` (2026-10-25); none is left (counted read-only
  2026-10-28), so no reader keeps one. A switch to the new model is
  not refused and counts nothing (`settings.change`); a switch to FaceNet is refused for every library, and so is an undo in History that would put it back, by the same
  rule and sentence (`settings.NOT_FACENET`, asked by `settings.undo_check` through `services.journal.why_not`;
  owner, 2026-10-28). The consequence is stated where the owner switches (the settings dialog's face model group,
  which must be acknowledged).
- **The face pass names nobody (2026-10-18, owner; DECISIONS 2026-10-18 "THE FACE PASS NAMES NOBODY")**: the new face
  model's pass (`services.face_pass`, deleted 2026-10-27 with `face-pass`: the index re-embeds after a switch) made its
  new faces unnamed and left a matched face's name as it was; Name faces from tags named the new faces after the gate.
  A photo's named faces paired first, so a named face kept its person beside an unnamed duplicate of it.

- **Retiring the old detector's faces (2026-10-22; DECISIONS 2026-10-22 and 2026-10-25)**: run once on each
  library (photo_index change 3247, kr-track 347, renton_parkrun 1225, counted read-only 2026-10-25): the faces
  holding no vector under the library's model deleted and every decision of the faces kept cleared, no keyword in a
  photo file and no node of the tree touched -- the people a photo's keywords name are what `cluster-faces` and Name
  faces from tags name the kept faces from again. The command is deleted (owner, 2026-10-25: the old model goes away
  completely); its change stays in the journals, its undo refused (`services.journal.RETIRED`), and a face pass's
  change recorded before it is refused too while it stands (`services.journal.pass_check`, 2026-10-26: those faces stand
  in for the old faces it deleted; photo_index 2,499 kept passes, kr-track 20, renton_parkrun 8, counted read-only
  2026-10-26); both checks stay until the baseline migration removes them.
- **The margin (2026-10-17, phase 2b; DECISIONS 2026-10-17)**: `looks_wrong` asks none -- a name already given is neither
  taken away nor questioned by it (owner's scope) and the person page reads one person's faces only; `face_tags._matched`
  keeps "exactly one person reaches 0.80" (FaceNet's naming likeness then; each model's own since 2026-10-20, AdaFace's 0.520) beside it, since the margin alone would name a face two people reach 0.04 apart, a
  name the old rule never gave (phase 2's gate), and leaves out the people the photo's other faces hold, as the pairing does.
  The photo with one face and one person is asked no margin at a save, where cluster-faces' last pass asks 0.04 over the
  library: a save is the owner's own act on that photo's tag, and the tag says whose its one face is; the last pass guesses
  among several faces of a photo whose keywords say only who is in it.

- **Elimination is not the cluster-of-one gate** (2026-10-17, phase 2c; DECISIONS 2026-10-17 "Phase 2c"): the gate is the
  vote's. The rounds' process of elimination -- one face left and one keyword person no face is known for, the photo's
  background faces not counted -- names that face with no likeness asked, as the owner's rule of 2026-10-15 (#1374) has it
  (`test_clustering_meets_naming`: `test_one_face_left_and_one_person_left_on_a_photo_is_still_named`, and the ties that
  name neither). A face the gate refuses may be named so: it is the known route for a person no face is known for, and
  trunk gave such a face the same name by the vote (today's names unchanged on copies of the three libraries).

- **The clustering trace's fourth decimal** (2026-10-16, phase 2a; #1548): the last pass read its likenesses from one float32
  product of a block of faces with everyone's faces, not one product a person, so a likeness differed in its last bits
  (at most 4.2e-07 on kr-track) and the trace printed 89 of photo_index's 187,757 similarities one apart at the fourth
  decimal. Since #1545 (2026-10-17) the person's page, Suggest and the last pass are one column of one table
  (`likeness_table(among=)`; `likeness_many` is that column), so they agree bit for bit. The names were and are identical
  face by face on the three libraries.
- **One gallery of who a face looks like** (2026-10-22, phase 4a cut 2, #1737): `clustering.FaceRows` -- the named
  or decided faces, a row a face, each with its photo's id and its year -- answers every matcher: the strip, the boxes,
  Identify's cards and a person's grid, automatch, Name faces from tags, Suggest and cluster-faces
  (`FaceRows.likeness`, a column a person; `likeness_of`, one person's column alone). `KnownFaces`, a second gallery
  holding the same faces again, is gone, and with it the case of #1611 (one person's faces held twice). By design: a
  face is compared with a person's closest face of the years around its own (the age-based window, `in_era`); its
  year is its photo's Date Taken alone (`dates.era_year`, 1950 to the current year, `store.faces.eras_of`), and a
  face with none is compared with every year, as is a person with no dated face; a face is never compared with
  itself nor with a face of its own photo (two faces in a photo are two people), wherever it is asked (owner and main
  session, 2026-10-22; DECISIONS 2026-10-22). A card whose person has no face left to compare is 0 and shows no
  number. Its rows are built one way (`FaceRows.made`: a zero vector left out, none in the three libraries). Two lazy
  lookups of a gallery (`FaceRows._where`, `columns`; `FacesByPerson._eras`) are filled without its lock: each is the
  same answer of the same rows, so two Suggest workers that build one at once build it twice and keep either (the
  grouping itself is made under the lock, as it is large). `FacesByPerson.table`'s loop a face at a time is slower for a
  whole folder held out at once, which nothing asks now (the gate that did was deleted 2026-10-28). The named matrix is rebuilt after any
  photo write (the photos stamp moves on any save): 1.25 s on photo_index, paid by the first boxes request after a tag
  save; by design (measured 2026-10-22).

- **#1209**, a tree rename stopped half way: the photos written hold the new tag while the tree keeps the old node. One
  place orders a tree edit against its files (`tags._retag`): the tree moves only once every photo is rewritten, the reply
  says "stays in the tag tree until every photo is rewritten: make the same edit again to finish the rest", and making it
  again finishes (`tests/test_tag_tree_edits_are_batches.py`, PersonRename). Moving the tree first would leave photos that
  name nobody when a write fails, which is worse.
- **#1294**, `store.faces.name` takes many ids with one caller passing one: the list is the same statement, and some 40
  tests name several faces with it; narrowing it changes nothing a person does.
- **#1426**, `Directory.of_name` and `of_reference`'s bare-name arm: 79 of kr-track's 546 stored Suggest rows hold a person as a
  bare name only it finds (counted 2026-11-08; 98 chips on 2026-10-15). The suggester writes the tag path now; the arm goes
  when the owner clears or runs those suggestions again, as he wishes.
- **`Ref` and `Person` stay two.** A `Person` is a node (always an id); a `Ref` is whom a keyword or a face names, and a
  keyword under a face root that no node holds names a `Ref` with no id: a photo read without its library
  (`PeopleVocabulary.defaults()`), which lists nobody for it (`people_rows`).
- **#1472's index half**: `idx_faces_name` goes with migration 33 (built 2026-10-18, DECISIONS "MIGRATION 33 IS PULLED FORWARD").
- **#1466**, TagTuner's two people reads, was closed `decided:` before this pass (no instance; 0 hidden people live).


## The owner's to answer

- **A picture size stored with each photo** (#2738, #2761): the one answer that lets the two jobs (Name faces from tags,
  cluster-faces and Suggest) ask the 2% rule the photo is shown by. It is a migration; not built until the owner chooses.
- **#1 and #831**, faces of a sideways photo: closed `decided:` (owner, 2026-10-16); DECISIONS 2026-10-27 has faces detected
  on the picture as shown, boxes kept stored, the panel drawing them turned (#1975).

## The unit files that stay, and the rule each holds

Python:
- `test_answers_carry_their_person`: every answer that names a person carries `{id, name, tag}`.
- `test_automatch_references`: automatch compares a face only with faces a person decided (#640).
- `test_automatch_writes_once`: automatch's write is one write, the tree read once (#659).
- `test_cluster_candidates`: grouping a block at a time, so the screen can say how far along it is.
- `test_clustering_evidence`: clustering's evidence is what people wrote, never its own last guess.
- `test_clustering_keeps_a_name_the_tags_confirm`: a name its photo's keywords confirm is kept below 0.80 (#855).
- `test_every_tag_has_an_id`: a keyword gets its node once; a store face writer refuses a name no node has.
- `test_excluded_faces_stay_unnamed`: an excluded face is never named, and a write says what it changed.
- `test_face_click_reads_named_faces_once`: a click on a face card reads the named faces once.
- `test_face_clustering_rules`: the resolution's anchors, votes, conflicts and dismissals.
- `test_face_exclusion`: the excluded bucket, its listing, and an excluded face out of identity work.
- `test_face_roots_are_the_trees`: which roots hold faces is said by the tree alone.
- `test_faces_from_tags`: a photo's person tag names its one face still to be named (#788).
- `test_folder_scope_roots`: a folder given to a backfill is read through the roots (#994).
- `test_group_tag_is_not_a_person`: a group tag names nobody; the rule, and in a library.
- `test_identify_cache_keeps_others_writes`, `test_identify_cache_sees_every_change`: the grid cache's fingerprint.
- `test_identify_queries`: what the Identify queries may touch on 225,000 faces.
- `test_index_resolves_people`: the index resolves people by the library's own tree.
- `test_journal_through_the_services`: the changes of the deleted merge of person tags and refresh of rows, which the live journals hold, list in History; their undo is refused by schema version (the file tests the journal's list and undo of changes of that shape at the current schema).
- `test_new_person_reads_the_pool_once`: New Person reads the nameless faces once per state (#5).
- `test_organize_faces`: what the server gives and takes for Organize's face boxes (#791).
- `test_people_by_id_tree`: rename, move, merge and delete of a person's tag, one transaction each.
- `test_people_representative_faces`, `test_person_face_samples`: the face shown for a person, and the four hovered.
- `test_person_ids`: the id beside the name: written, settled, kept through renames.
- `test_person_rename_reaches_the_files`: a person renamed reaches the files and says how many.
- `test_reindex_keeps_face_decisions`: re-indexing a photo keeps its faces' decisions.
- `test_server_lists_are_alphabetical`, `test_tag_order`, `test_tag_sort_key_edges`: the order shared with the pages.
- `test_service_faces`: the faces service's own refusals, fingerprints and the detection settings.
- `test_service_tags`: the tag tree's edits, files stood in for.
- `test_shared_taxonomy_routes`: the tree's routes both apps serve alike.
- `test_store_taxonomy_add_path`: the one writer of new nodes.
- `test_tag_view_api`: Review Tags' endpoints.
- `test_tags_are_checked_where_they_are_set`: a tag that breaks the rules is refused by both servers.
- `test_tags_from_faces`: the backfill of a named face's tag (#861).
- `test_taxonomy_edits_report_what_they_wrote`: a rename or delete counts what it rewrote.
- `test_trace_file_per_library`: each library keeps its own clustering trace.
- `test_tuner_photo_copy_upright`, `test_tuner_reads`, `test_tuner_server_api`: what TagTuner's routes answer.
- `test_vocabulary`: how a tag is spelled and taken apart.

Pages (`tests/frontend/<name>.test.mjs`): `face-strip` (the details panel's faces), `grid-build-progress` (how far a grid
is), `identify-faces` (the panel and its candidates), `identify-faces-cluster-buttons` (a cluster's buttons act on the
faces still in it), `identify-faces-counts` (faces counted, a mixed selection by its badges),
`identify-faces-in-place-removal` (no rebuild on ignore), `library-sort-and-organize-header` (#712 to #714), `name-faces`
(the one dialog of Name faces from tags), `organize-faces` (#791), `person-directory` (people by id on the pages),
`person-faces` (four faces on hover, #790), `person-filing-table` (one table with the server), `person-names` (a name
shown, the tag where filed), `person-tag-form` (a person written as a tag path), `review-dialog` and `review-from-activity`
(folders to review), `show-matched-toggle`, `tag-editor-in-place`, `tag-order`, `tag-view`, `tag-vocabulary`,
`tagpup-people-by-id` and `tagpup-people-lists-by-id` (two people called alike), `tags-shown-alphabetical`, and TagTuner's
`tuner-assign-label-tells-the-truth`, `tuner-face-zoom`, `tuner-hidden-people-exist`, `tuner-lists-alphabetical`,
`tuner-new-person-group`, `tuner-people-by-face`, `tuner-people-by-id`, `tuner-reexamine-folder`,
`tuner-refresh-keeps-the-grid-loading`, `tuner-select-all-is-linear`, `tuner-starts-last`,
`tuner-suggestions-belong-to-their-grid` and `tuner-undo-puts-faces-back`, each the rule its name says.


## History

One line each; the decision and its reasons are in docs/DECISIONS.md.

- 2026-11-12: one rule of the face in the background, 2% of the stored picture size, everywhere; no stored size, no
  background (DECISIONS 2026-11-12; #2738, #2761).
- 2026-11-08: a person at the top of the tag tree is legal (DECISIONS 2026-11-08).
- 2026-11-08: the 2% rule has one home, `clustering.is_background` (5492cb67); the job's pixel rule waits (#2738, #2761).
- 2026-11-02: a face under 2% of the picture's shorter side is not shown on the photo (DECISIONS 2026-11-02).
- 2026-11-02: eleven bare top-level people of kr-track merged under People (docs/watching.md #2360).
- 2026-11-02: naming a face is shown at once and written behind the photo's queue; the selection's Suggested People lists
  who the faces look like (commits aaf1d8d2 to 475944c8, 75a859ec).
- 2026-10-28: the old detector's faces are gone and the face pass deleted (DECISIONS 2026-10-22, 2026-10-25, 2026-10-27).
- 2026-10-23: an index is followed by Re-examine's matcher over the faces it made (DECISIONS 2026-10-23; F11).
- 2026-10-20, 2026-10-17: the cuts are the face model's; a name on likeness alone needs a margin (DECISIONS 2026-10-17,
  2026-10-20).
- 2026-10-15: the domain pass, the vocabulary from `person_ids.People`, one maker of a person (DECISIONS 2026-10-15; docs/history/).
