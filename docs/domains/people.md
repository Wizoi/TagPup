# People and faces: the state of the domain

The domain's charter is `.claude/agents/tagpup-owner-people.md`: its Scope, the contract the other domains rely on, its
invariants and guards, and its scenarios P1 to P7. This page states each of the seven conditions of health
(docs/OWNERS.md) with its evidence and date. A condition not met is the domain's queue.

## What the domain holds (counted 2026-10-15, read-only)

| Library | People | Groups | Face roots | Faces | Named | By hand | Excluded | Listed people | From a face alone |
|---|---|---|---|---|---|---|---|---|---|
| kr-track | 78 | 1 | 12 | 9,850 | 1,640 | 1,531 | 5,788 | 1,981 | 1,052 |
| photo_index | 411 | 12 | 7 | 187,784 | 35,425 | 175 | 27 | 79,586 | 230 |
| renton_parkrun | 257 | 1 | 1 | 3,328 | 1,212 | 1,071 | 101 | 1,264 | 189 |

People at the top of the tree: 11, 4 and 0. Leaves two people share: 0, 2 and 0. A face with a name and no id: 0 in all
three; a listed person with no id: 0 in all three; a person hidden from autocomplete: 0 in all three. Saved Suggest chips:
601, 5 and 199, of which 103 in kr-track hold a bare name (98 of them found only by `Directory.of_name`).

Face vectors (migration 33, 2026-10-18) are in `face_embeddings`, one per face and face model. Every reader of vectors is
given the key it reads under (a required argument): a decision reads the library's `faces.model` once
(`store.faces.model_key`, on its own connection), reads its vectors under it and decides by that key's cuts; a set of
vectors kept between reads carries its key (`clustering.FaceRows.key`, `identify.UnnamedFaces.key`) and is compared only
with vectors of the same key (`FaceRows.under`), so a switch committed meanwhile never sets one model's vectors beside the
other's, nor reads them by the other's cuts. The join with a vector
(`store.faces.WITH_VECTOR`, `HAS_VECTOR`) is the one place that leaves a face without one out of what compares faces:
clustering, New Person's pool, the Identify queue and grid. Asked who such a face resembles, Identify answers one
sentence (`identify.NO_VECTOR`, 400), shown by the pages; cluster-faces says how many faces took no part.

## The seven conditions (2026-10-15)

1. **No reach-ins.** Met. The uses of People's internals by other domains are rows of the Integration charter's contract
   table: `core/vocabulary` (row 60), `people_vocabulary` / `read_people_vocabulary` (61), `face_refs` / `crop_of` /
   `cache_crop` (62), `taxonomy.seed` (63), each with its guard test.
2. **No rule written twice.** Met.
   - Who is a person and who a group: `person_ids.People`, alone. `taxonomy.vocabulary_of` builds the `PeopleVocabulary`
     from its listing and groups; `PeopleVocabulary.from_rows` (its own roots, categories and groups) and
     `taxonomy.group_tags` are gone (#1463). Compared on the three libraries before and after: the roots, groups, people,
     ids and every photo's people (62,327 photos) are identical.
   - Who makes a new person: `face_people.made`, the tag first (#1252). The store's face writers refuse a name no node has,
     nothing written; `ensure_node` makes keywords' nodes only.
   - The settings a detection is recorded under are held equal to those a face model is made from
     (`tests/test_service_faces.py`, ADetectionIsRecordedUnderTheModelsSettings); the two spellings cannot import each other
     (an import cycle, or torch).
3. **No hidden coupling.** Met. Clustering read a photo's people as ids in a statement of its own and wrote them over the
   records' `people`, which every other reader takes as names (#1372, #1373). `store.photos.records(people_ids=True)` answers
   them under `people_ids`, in the statement that reads the keywords, with no names built (the same plan, 0.34 s over
   photo_index's 59,519 rows).
4. **No concept that could collapse.** Met. `person_ids.wire` is gone (#1482): a person travels as the id of their node, and
   only the doors take a typed name (`faces.who_is`, `face_people.made`). `face_people.shown` and `store.people.listed_ids`
   are gone. `Ref` and `Person` stay two: by design, below.
5. **No branch without an instance.** Met. Deleted, no instance: the store's node for a typed name; `ensure_node`'s person
   arm; `detector_of`'s reading of a missing setting as None (only stand-in models lacked them, #1499); a detected face's
   `name` (no face model sets one); `from_rows`' arm for a path whose parent is not in the tree (0 live). Kept, with the
   user action or the count: a typed name (the pages' New Person dialog and the face box's typed name: neither the CLI nor the MCP names a face); `Directory.of_name` for the 98 saved
   chips (#1426, below).
6. **Scenario tests, each file owned and mapped.** Met by the owner's bar (DECISIONS 2026-10-15, recommendation 1): P1 to
   P7 each have files in `tests/owners.py`. This pass deleted `tests/test_people_vocabulary.py`, which restated rules held
   elsewhere: three mutations of what it asserted (a bare name names the person; a group or face root is a person; the tree's
   roots are not read) each fail tests that stay, the D5 scenario `test_photo_people_follow_their_sources` among them. The
   unit files that stay, and the rule each holds, are listed below.
7. **No open data or wrong finding, and every low fixed or by design.** Met once this branch is merged, but for #1 and #831,
   whose status is the owner's (below): fixed #1188, #1252, #1293, #1297, #1372, #1373, #1463, #1467, #1481, #1482, #1499,
   #1504; by design #1209, #1294, #1426, the idx_faces_name half of #1472; #1436, #1471 and the charter half of #1472 are
   the main session's (the agents' definitions).

## By design (each row closed `decided:`)

- **A run's decided vectors and the library's key (2026-10-18, migration 33)**: naming by tag reads face vectors under
  the library's face model, read once per write (`faces.model_key`), while `derived.Batch.decided` keeps each person's
  decided vectors for the run. A switch of `faces.model` mid-index leaves the run on the face model it began with
  (its vectors, and the cuts it decides them by: the key travels with them; cluster-faces likewise), so the rest of the run's faces have no vector under the new key: each is left
  (`unreadable_face`), or named by its photo's one keyword person where that person has no decided face under the new
  key and no other photo waiting (#839), the tag's own route. Two models' vectors never meet in one product; the next
  index or cluster-faces reads them again.
- **Identify on a library switched to the new face model (2026-10-18; 2026-10-20)**: every reader compares faces under the
  library's model, so a face with no vector under it takes no part, with no sentence of its own in the queue, a person's
  grid or the person page: an old face the pass matched with nothing it found (1,195 of kr-track's, counted 2026-10-20),
  and a face in a photo the pass has not reached. A switch is refused while the latter are more than 5% of the faces
  (`services.settings.UNPASSED_SHARE_ALLOWED`) -- an undo in History that would put the new model back too, by the
  same check (`settings.undo_refusals`, asked by `services.journal.refusals`) -- and says either way how many named faces would leave the gallery
  (`settings.change`, `details["face_vectors"]`); the consequence is stated where the owner switches (the settings
  dialog's face model group, which must be acknowledged); a face asked about directly says why (`identify.NO_VECTOR`).
- **The face pass names nobody (2026-10-18, owner; DECISIONS 2026-10-18 "THE FACE PASS NAMES NOBODY")**: the new face
  model's pass (`services.face_pass`) makes its new faces unnamed and leaves a matched face's name as it was; Name faces
  from tags names the new faces after the gate. A photo's named faces pair first, so a named face keeps its person
  beside an unnamed duplicate of it.

- **The margin (2026-10-17, phase 2b; DECISIONS 2026-10-17)**: `looks_wrong` asks none -- a name already given is neither
  taken away nor questioned by it (owner's scope) and the person page reads one person's faces only; `face_tags._matched`
  keeps "exactly one person reaches 0.80" (FaceNet's naming likeness; each model's own since 2026-10-20) beside it, since the margin alone would name a face two people reach 0.04 apart, a
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
  session, 2026-10-22; DECISIONS 2026-10-22). A card whose person has no face left to compare shows 0.

- **#1209**, a tree rename stopped half way: the photos written hold the new tag while the tree keeps the old node. One
  place orders a tree edit against its files (`tags._retag`): the tree moves only once every photo is rewritten, the reply
  says "stays in the tag tree until every photo is rewritten: make the same edit again to finish the rest", and making it
  again finishes (`tests/test_tag_tree_edits_are_batches.py`, PersonRename). Moving the tree first would leave photos that
  name nobody when a write fails, which is worse.
- **#1294**, `store.faces.name` takes many ids with one caller passing one: the list is the same statement, and some 40
  tests name several faces with it; narrowing it changes nothing a person does.
- **#1426**, `Directory.of_name` and `of_reference`'s bare-name arm: 98 saved Suggest chips of kr-track hold a person as a
  bare name only it finds (counted 2026-10-15). The suggester writes the tag path now; the arm goes when the owner clears or
  runs those suggestions again, as he wishes.
- **`Ref` and `Person` stay two.** A `Person` is a node (always an id); a `Ref` is whom a keyword or a face names, and a
  keyword under a face root that no node holds names a `Ref` with no id: a photo read without its library
  (`PeopleVocabulary.defaults()`), which lists nobody for it (`people_rows`).
- **#1472's index half**: `idx_faces_name` goes with migration 33 (built 2026-10-18, DECISIONS "MIGRATION 33 IS PULLED FORWARD").
- **#1466**, TagTuner's two people reads, was closed `decided:` before this pass (no instance; 0 hidden people live).

## The owner's to answer

- **#1 and #831**, faces of a sideways photo: DECISIONS 2026-10-07 un-tabled the redesign (boxes stored as the photo
  displays). It is a coordinate redesign, new work under the feature freeze; it is neither fixed nor by design until the
  owner chooses when.

## The unit files that stay, and the rule each holds

Python:
- `test_answers_carry_their_person`: every answer that names a person carries `{id, name, tag}`.
- `test_automatch_references`: automatch compares a face only with faces a person decided (#640).
- `test_automatch_writes_once`: automatch's write is one write, the tree read once (#659).
- `test_cluster_candidates`: grouping a block at a time, so the screen can say how far along it is.
- `test_clustering_evidence`: clustering's evidence is what people wrote, never its own last guess.
- `test_clustering_keeps_a_name_the_tags_confirm`: a name its photo's keywords confirm is kept below 0.80 (#855).
- `test_dedupe_faces_keeps_decisions`: a merge of two copies of a face keeps the decided one and every exclusion.
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
- `test_journal_through_the_services`: the maintenance services journal what they apply.
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

- 2026-10-20, phase 3 merge 3, items 1 and 2: the cuts a name is given and offered at are the face model's
  (`clustering.Cuts`, `CUTS`, `cuts_of`; FaceNet's 0.80 / 0.04 and 0.70 / 0.02 unchanged, AdaFace's 0.520 / 0.205 and
  0.310 / 0.090 from the gate), asked by every reader by the key its vectors were read under; Name faces from tags'
  counts renamed `like_them_enough_to_name` / `like_them_enough_to_offer`. A library may switch to the new model once its
  pass has reached all but 5% of its faces (`settings.change`, docs/AI_PIPELINE.md).

- 2026-10-17, phase 2c: the vote of a cluster of one in a photo of two or more faces asks the tag's gate
  (`names_from_the_tag`) for a name it gives, in cluster-faces' first round past the photo's conflicts; a name the face
  carries is never asked, an anchor (one face, one keyword person) and a cluster of two or more are not asked. The rounds and
  the last pass read who each person is in one place (`identities._known_faces`, anchors first). `min_samples` stays 1.
- 2026-10-17, phase 2b: a name resting on likeness alone needs a margin over the next person named as well
  (`clustering.margins`, NAME_MARGIN 0.04, OFFER_MARGIN 0.02; owner): the pairing and the last pass of cluster-faces, a save's
  tags naming one of several faces, Re-examine, New Person, Suggest, the strip, the boxes and the Identify grid. A name a face
  carries or one given by hand is asked none; the photo with one face and one person is not asked one (the tag says whose
  it is); a name already given is not flagged by it (`looks_wrong`). The flat matrices are grouped by person in one place
  (`clustering.FacesByPerson`); `tests/test_face_values_have_one_owner.py` fails a top-of-a-row taken elsewhere.
- 2026-10-15, the domain pass:
  - the vocabulary from `person_ids.People` (#1463); `wire` gone (#1482); one maker of a person (#1252);
  - clustering's people as ids under their own key, in one statement (#1372, #1373);
  - the specs without the deleted command (#1293); INVARIANTS' guards;
  - fixtures through the real writers (#1297, #1481, #1467), the stand-in models with their settings (#1499), the XMP
    person-field tests gone (#1504), a test named for what it tests (#1188), one duplicate unit file deleted.
