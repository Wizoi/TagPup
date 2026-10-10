# TagPup architecture, history: Identity by id

Moved verbatim from `docs/ARCHITECTURE.md` on 2026-10-09 (trunk 17afc1b), when that file became a map.
Nothing in the sections below was edited, so a number, a date or a branch name in them is as it was on
the day it was written: read it as a log, not as the current design (the map, [../ARCHITECTURE.md](../ARCHITECTURE.md),
and [../INVARIANTS.md](../INVARIANTS.md) are current). Index: [README.md](README.md).

Contents: Identity by id (stage 1) and People by id, stage 2: a person is a tag-tree node's id.

Sections in this file:

- Identity by id *(owner, 2026-10-02; `photo_tags` built in 9a-1; stage 1, the id beside the name, built 2026-10-04 on `arch/identity-by-id`, migration 21; stage 2 design)*
- People by id, stage 2 *(design, 2026-10-09; built 2026-10-09: part A, phases 1-3, part B, phases 4-5, and part C, phases 6-8; the questions at its end were the owner's, answered below)*

---

### Identity by id *(owner, 2026-10-02; `photo_tags` built in 9a-1; stage 1, the id beside the name, built 2026-10-04 on `arch/identity-by-id`, migration 21; stage 2 design)*
Today a person is a leaf name in `faces.name`, `photo_people.name` and the suggester, and a tag
is a path (`People/<name>`) in the files and in `photos.tags`; CLAUDE.md's rule exists because
every site converting between the two by hand shipped a bug. `tag_taxonomy` already gives every
node an integer id and a parent id, and a person is a node with `has_face` set. The design:
- **Files, pages and the API keep names and paths.** XMP keywords are text and the files are the
  source of truth; nothing outside the store learns an id. The conversion lives at the store
  boundary, as root-relative paths do (`to_row` / `from_row`), in one module, with the same
  single-owner test.
- **`photo_tags(photo_id, tag_id)`** (9a-1, built 2026-10-02) and, in a later migration, **`photo_people`
  and `faces` naming a person by `tag_id`** (a column beside `name` first, filled and checked,
  then the name column read from the taxonomy). That later migration touches the 225,000 faces
  of photo_index and every identity path, so it follows 9b-9e and is asked about before it runs.
- **What an id fixes:** two people of one name filed in different branches (the ambiguous
  person path, #27) are two nodes; renaming a person is one taxonomy change in the database,
  and merging two is one id merge (the files still take their keyword rewrites, as now);
  counts and the navigator key on the id and survive a rename. Storage is a minor gain: the
  database is 2.55 GB, nearly all crops, vectors and metadata.
- **What must hold:** ids are stable. The taxonomy lives only in the database
  (`*_taxonomy.json` is an export), so a re-import or a rebuild from files must find a node by
  its path and keep its id, and add a node, never reuse an id, for a tag a file holds that the
  tree lacks. A node deleted in the tree leaves no `photo_tags` row naming it (cascade), and a
  file still holding its keyword makes the node again with a new id on the next read.
- **How it fails, to be tested before it is built:** a tag in a file the tree lacks; a rename of
  a person or branch while an index run or Suggest is running; two libraries sharing one name
  with different ids; the tree edited in TagTuner while a bulk tag write is under way; an
  undo that replays a name recorded before the change; a restore of a snapshot taken before ids
  were used; a backfill read of 225,000 faces (one lookup per name, not per row).

**Stage 1, built** *(2026-10-04; the owner moved it before 9e)*. Additive: `faces.tag_id` and
`photo_people.tag_id` sit beside `name`, and **every read still reads the name**. One module,
`tagpup.store.person_ids`, owns the conversion and every write of the column, as `paths.to_row` /
`from_row` own a path's (`tests/test_person_ids_single_owner.py`):
- **The rule.** A person is a node with `has_face` set that is not a root (a root holding faces is a
  category, as `PeopleVocabulary` reads it) **and has no node under it: a branch tag cannot be a person**
  *(owner, 2026-10-04; #660)*. A name is that node when exactly one such node is called it, compared as
  names are compared everywhere (`vocabulary.key`: trimmed, without case). A name no person node is
  called -- none at all, or only a branch -- or that two are (#27), gets NULL: never a guess, and the
  name itself is left as it is (stage 1 renames and unnames nothing). The doctor lists those names by
  count, and by name only with `--show` ("names with no person node", "names with several person
  nodes", "names on a branch" with the branches' ids); they are reported, not broken, and settling them
  is the owner's. A person given a tag under them becomes a branch and loses the id, by the tree's
  edit; a leaf and a branch of one name is the leaf. Counted read-only on 2026-10-04, by the leaf rule:
  photo_index's faces hold 284 distinct names (35,803 named of 226,246 faces), 277 of them one person,
  4 none (40 faces), 2 on a branch (7 faces), 1 several (1 face); its `photo_people` 413 names (80,060
  rows), 404 one, 4 none (40 rows), 4 on a branch (65 rows), 1 several (12 rows); it has 9 has_face
  branches. kr-track: faces 73 names, 61 one, 12 none (198 faces); `photo_people` 75, 62 one, 13 none
  (200 rows); no branch. Before the leaf rule, the three rules weighed -- exact name, `vocabulary.key`,
  and `find_person_path`'s (a leaf under a face root) -- gave the same counts on every library; nothing
  on any of them differs only in case or spacing, and every node's `name` is the leaf of its `tag`.
- **The id is derived in stage 1**: what the name gives now, under the tree as it stands. So it is
  kept wherever either changes, **in the same transaction**, with the tree read inside it and never
  cached across transactions (a rename committed by another process between two writes is what the
  second reads): every writer of the faces store, through `faces._rebuilt` (`follow_faces`, the faces
  of the photos it touched); `people.rebuild` (`follow_listed`); `people.rename` (`follow_names`);
  every tree edit, through `people.tree_edit` (`follow_tree`: the tree's people read before and after,
  and every name matched again only when they differ -- two reads of ~400 nodes otherwise); and the
  journal's `_derive` after an apply, an undo or a settle (`follow_faces` for the photos whose faces it
  wrote, `sync` when it wrote the tree). Sync, index reads, Suggest's apply, automatch, clustering and
  file changes reach it through those; `file_only` writes no row.
- **Not a key yet, so a rename has a window.** TagTuner's rename moves the node (one transaction),
  rewrites the files, then renames the faces (another). In between, the faces' old name names no node
  and their id is NULL -- never the renamed node's under a name it no longer has. Stage 2 removes the
  window: the rename is then the node's alone.
- **The journal.** `tag_id` is a derived column of `faces` (`journal.DERIVED_COLUMNS`): an undo never
  holds a row to it, a face put back by an undo -- one recorded before the column existed among them --
  is given its id by `_derive`, and a rehearsal compares it after `_derive` on both sides. Migration 21
  adds only that column to a journaled table (`schema.ADDS_DERIVED_COLUMNS`), so it blocks no undo of a
  change made before it (`journal.schema_gap_blocker`); a test holds the declaration to the columns the
  migration really adds.
- **Migration 21** adds the two columns and `idx_faces_person` (`name`, `tag_id`) first, then fills
  them by `person_ids.sync`: the pairs of name and id held, read from the index (never a face's row or
  vector), one UPDATE per pair that is wrong, by the index. One transaction under the write lock: a
  crash leaves the library at 20, and the next open runs it again. **Measured** on a sandbox copy of
  photo_index (68,466 photos, 226,246 faces), through the runner: **17.5 s** the first time (a copy
  just written, cold); warm, the steps are the index 1.6 s, the backfill 1.8 s (35,762 faces and 80,008
  listed people given an id), and the runner's standard checks on `faces` 6.5 s (quick_check 2.8 s,
  foreign_key_check 3.7 s). Writers in other processes wait (the busy timeout is 30 s). **It runs as the
  server starts** (#661): `tagpup_web` starts a thread, before it serves, that brings every library it
  serves up to date (`libraries.bring_up_to_date_in_background`). The server answers at once --
  `/api/server`, which the supervisor's hand-over waits 60 s for, names no library, and lists the thread
  as busy ("bringing N library(ies) up to date"), and a drain waits for it (#664). A page opened while a
  library migrates gets a blank tab until the migration ends (every URL under the library's name waits on
  its write lock); a page already open shows its own spinner for its requests (#666). Before, the first
  request naming the library ran the migration itself. A library held past the busy timeout is logged and
  left for its first request. The CLI and the MCP still migrate a library when they first open it. A whole re-sync
  afterwards, as a tree edit that changes who the people are does, takes 0.23 s. The plans, checked on
  the copy: the pairs `SEARCH faces USING COVERING INDEX idx_faces_person`; the UPDATE `SEARCH faces USING
  INDEX idx_faces_person (name=? AND tag_id=?)`; a photo's faces `idx_faces_photo_id`; `photo_people`'s
  pairs a scan of its 80,060 small rows by `idx_photo_people_name`. The new index also serves every
  lookup `idx_faces_name` served (the planner now picks it, covering).
- **What stage 1 cannot see.** A version of the app from before 21 still writing to the library writes
  names without ids; the doctor's `face_person_ids_out_of_step` and `listed_person_ids_out_of_step`
  (in `checks.RULES`, so the MCP's checks too; the examples are face ids and photo ids) count them, and
  `tools/doctor.py --rebuild-derived --apply` puts them right with the derived tables, touching no name.
  A snapshot from before 21 restored comes back at 20 and is migrated again by `snapshots.restore`'s
  `schema.ensure`. Two libraries are two trees: the same person has an id in each, and nothing reads an
  id across libraries. Ids are AUTOINCREMENT, never given again, so an id left behind by a node deleted
  by an older writer names nothing rather than someone else.

**Stage 2** *(designed and built 2026-10-09, in three parts; the owner's answers are at the end of the design)*: the id becomes the key and the name a cache of the
tree's, the same name can be two people under two groups, and a picker shows the group when a leaf is shared. The design, the counted
numbers, the phases and the owner's questions are in "People by id, stage 2" below. It replaces this section's earlier plan, whose
decisions it keeps: a branch tag cannot be a person, the two rules are refusals, deleting a person tag that faces use is refused with a
force that unnames them, old history stays undoable, and (#985) a name without a person tag is listed for the owner, never unnamed.

### People by id, stage 2 *(design, 2026-10-09; built 2026-10-09: part A, phases 1-3, part B, phases 4-5, and part C, phases 6-8; the questions at its end were the owner's, answered below)*
Stage 1 put the id beside the name and kept it derived from the name. Stage 2 makes the **id the person** and the name
what the tree calls them. Two things the owner added on 2026-10-09 shape it: the same NAME can be two people when their
tags sit in different groups (two cousins called Sam under `Family/Thackeray` and `Family/Ingersoll`; a dog and a friend
called Max under `Pets` and `Friends`), so identity is the tag's id and the leaf is only a label; and wherever two people
share a leaf, every picker, list, chip and hover shows the group too, and only then.

**What stays.** A person is a leaf `has_face` node that is not a root (stage 1's rule, unchanged). The files and
the keywords in them stay text: a person keyword is a path (`Family/Thackeray/Sam`), and a path names exactly one node,
so reading a file resolves a person without any guess even when the leaf is shared. Ids never go into a file, a
journal row of another library, or a snapshot's meaning; they are per library, as now.

**Counted 2026-10-09, read-only (`db.readonly_uri`), by the leaf rule.**

| | photo_index | kr-track | renton_parkrun |
|---|---|---|---|
| faces / named | 226,208 / 35,796 | 9,850 / 1,640 | 3,328 / 1,211 |
| nodes / face roots / person leaves / face branches | 895 / 3 / 406 / 9 | 73 / 1 / 66 / 0 | 262 / 1 / 257 / 0 |
| **person leaves shared by 2+ person tags** | **1 leaf, 2 tags (under two different roots)** | **0** | **0** |
| faces naming a shared leaf (photos) | 1 (1) | 0 | 0 |
| `photo_people` rows naming it (photos) | 12 (12), all from a keyword, the path names the node exactly | 0 | 0 |
| named faces with `tag_id` NULL, by why_not | none 40 (4 names), several 1 (1 name), branch 0 | none 186 (11 names) | 0 |
| `photo_people` rows with `tag_id` NULL, by why_not | none 40 (4 names), several 12 (1 name), branch 65 (4 names) | none 188 (12 names) | 0 |
| distinct ids on faces | 277 | 64 | 242 |
| face branches (group tags) carried by photos | 4 tags, 65 photos | 0 | 0 |
| tags of any kind with tags under them, carried by photos | 37 tags, 10,864 photos | 0 | 0 |
| `change_rows` on `faces` in the journal | 0 | 0 | 0 |

What the numbers say. (1) The owner's case is real but nearly empty today: one shared leaf, in photo_index only, and the
faces cannot say which of its two tags they mean (that one face and the 12 photos whose keyword path names one of the two tags are all the data the feature touches today). (2) The branch names are no longer on faces (the 7 were unnamed on 2026-10-09); what remains of "branch" is 65
`photo_people` rows made by group tags on photos, which #986 stops listing at all. (3) What is left for the names-to-review
list (#985): photo_index 5 names (4 with no person tag, 40 faces; 1 on two tags, 1 face), kr-track 12 names (186 faces),
renton_parkrun none. (4) **Rule (a), read as "no tag that photos carry gets a child", is already broken by 37 tags carried
by 10,864 photos in photo_index** (ordinary hierarchical keywords: a photo holds both `Places` and `Places/Seattle`); read
as "no PERSON tag that photos or faces carry gets a child" it is broken by the 4 group tags only. That is question 1.
(5) The journal holds no face row in any library, so "history from before ids" has nothing to translate today; the rule
below is for entries made before the build. (6) A lookup of a person's faces by id has no index: `WHERE tag_id = ?` scans
`idx_faces_person` (name, tag_id) in full and `photo_people` has none, so the migration adds them.

#### The id is the key: what moves
- **Writers.** Naming a face is `faces.name_as(conn, face_ids, person_id)`: it writes `tag_id` and, beside it, `name` (below), in
  one UPDATE. Nothing writes a face's person by a name any more except the one door that takes a name from outside
  (`person_ids.resolve`, below), so `tests/test_person_ids_single_owner.py` becomes "one writer of the pair". Face writers
  today, among them: `faces.name`, `name_if_unnamed`, `name_unnamed`, `set_names`, `reinstate`, `revert_automatic`, automatch's and
  clustering's, `identities`, the indexer's `record_detected`; all end in `faces._rebuilt` already.
- **Readers (158 lines that read `faces.name` or `photo_people.name`; 48 of them in `store/faces.py`, 16 in `services/faces.py`).**
  Everything that means "this person" groups, counts and joins by `tag_id`: `count_named`, `person_embeddings`, `person_page`,
  `counts_by_name` (becomes `counts_by_person`), `named_embeddings`, `for_named_matrix`, `named_for_known`, `names_by_photo`,
  `named_elsewhere_in_photo`, `guesses_named`, `identify.named_faces` / `decided_faces` / `representative_faces` / `face_samples`
  (keyed by id, not `{name: ...}`), automatch and clustering's known sets, `library_view`'s person source and counts, the
  navigator's people counts, `people.names`. `photo_people(tag_id, photo_id)` serves the photo side. The naming rules that compare
  "a person already on this photo" compare ids: a photo can hold two faces of two different Sams.
- **What a reader is shown** is a label (see "Showing the group"), never the cache.
- **The wire.** Between the pages and the server a person is `{id, name, tag, group, shared}`; a request names a person by
  `person_id`. The old `person_name` (a leaf) is still accepted by the CLI, the MCP and a page not yet reloaded: it resolves to the
  one person of that name, makes the person when no node has the name (today's `vocabulary.person_tag` rule: under the one
  face root, asked when there are several), and is **refused naming the candidates** when two are called it. A saved or
  bookmarked search keeps its contract (`{kind: 'person', value}`, phase 9e-1) with `value` the person's tag path; a bare name
  still works when it is unambiguous. A stale `person_id` (merged or deleted in another tab) is a 404 that says so and offers a reload; it never
  creates anyone.
- **`faces.name` stays, as a cache of the node's leaf, and identity never reads it.** Reasons to keep it: it is the only place
  an unresolved name lives (below); the CLI, the MCP and the doctor print it without a join; an older reader of a restored
  snapshot still sees names; and nothing is dropped, so nothing is one-way. Its rule: for a face with `tag_id` it is the node's `name`,
  written by `person_ids` alone in the same statement that writes the id, and by the tree edit that renames the node
  (`UPDATE faces SET name = ? WHERE tag_id = ?`, by `idx_faces_tag`); for a face with `tag_id` NULL and a name it is an
  **unresolved name**, left exactly as it is. The doctor's `face_person_ids_out_of_step` is turned round to compare
  `name` with the node's: id to name, the reverse of stage 1. (Dropping the column and joining the tree for every display was
  weighed: it ends the cache's drift for good, but an older app reading the library would see every face unnamed and offer
  them all to Identify, so it is not taken until nothing older is in use.) `photo_people.name` is the same cache.
- **Derived tables.** `photo_people` stays derived and stays written only by `people.rebuild`; its key becomes `(photo_id,
  position)` holding `tag_id` first and `name` as the cache. The one rule is `vocabulary.people_in_photo`, which returns person
  *references* (`id`, `name`, `source`) from three inputs: a keyword (its **path** is looked up in `PeopleVocabulary.by_keyword`,
  now built from `(id, tag, name)` rows, so a path gives its id; a bare-leaf keyword gives an id only when one person has
  that leaf, else an unresolved name), a person field of the metadata (a name: same bare rule), and the photo's faces
  (`tag_id`, exact). Duplicates are removed by id, not by lower-cased name, so a photo can list two Sams; a **group tag (a branch)
  is not a person** and gives nothing (#986). `photos.people` JSON (`PEOPLE_JSON`) keeps listing names; a photo that lists a
  leaf twice shows it twice, and the pages read ids for the label (`details-panel` chips).

#### Tree operations, by id
All of them are one transaction (tree row, `faces` cache, `photo_people` of the affected photos), with the files rewritten after it
by the machinery that exists (`tagging.replace_tag`, the journal's `change_files`, resumable). That closes stage 1's rename window:
no moment has faces with a name that no node has.
- **Rename a leaf** = `move_branch` of one node to a free path under the same parent. The id does not change; the cache follows. A rename
  to a path that is a node is refused ("that person is already filed there: merge them instead"). Changed meaning: today's Rename Person
  renames every node called the old name and silently joins them when the new name exists; stage 2's renames the one person picked
  (`rename_person(person_id, new_name)`) and never merges (question 8).
- **Move a person between groups** (`Family/Thackeray/Sam` to `Family/Ingersoll/Sam`): the node keeps its id; every face and every list
  entry follows by id; `faces` is not written at all (the id and the leaf are the same). The photos' keywords are rewritten to the new path.
  If another person called Sam is already at the destination the path is taken: refused, the owner merges or renames first.
- **Merge A into B** (two tags that are one person): `UPDATE faces SET tag_id = B, name = B.name WHERE tag_id = A`, the same on
  `photo_people`, the files' keywords rewritten, then A's row goes (`move_branch`'s join). The plan (dry run, as `tags.merge` already is by
  default) says how many photos would end up with two faces named the same person (both faces stay named; question 6).
- **Delete a person tag that faces use**: refused, with the count ("12 faces are named Sam; unname them first, or delete with
  force"). `force` unnames the faces in the same transaction (`faces.unname_person`, name_source as `unname` writes it) and journals it as one
  change with the tree row. A trigger `person_tag_not_deleted_while_named` (BEFORE DELETE ON `tag_taxonomy` when a face has that `tag_id`,
  RAISE(ABORT)) is the backstop for any connection; the merge repoints the faces before its DELETE, so the trigger sees nothing.
- **The two rules, as refusals with an explanation** *(owner, 2026-10-04 and 2026-10-07)*. Both are enforced where a person is chosen or
  a node made **by an action of the owner**, never when the indexer reads what a file holds: a file's keywords are the truth, are not
  refused, and make their nodes (a reading that makes a person tag a branch, or puts a person under a group, is shown by the doctor).
  (a) *A person tag that faces or photos carry gets no children.* Checked in `taxonomy` (one function, `refuse_child_of_person`) at
  `tags.create`, `move_branch` into a person, `Filer`/the keyword writer adding a new level under one, and the bulk Tags edit:
  "Sam is a person (12 faces, 40 photos); a person cannot have tags under them. Choose another group." Scope is question 1.
  (b) *A group tag is never put on a photo as a person.* Checked in `person_ids.resolve` (the one door for a person by id or name) so
  naming a face, `face_people.add_people`, the bulk People edit and the MCP all refuse a branch: "Family/Thackeray is a group of
  people, not a person." The pickers never offer a branch as a person. A group tag put on a photo as an ordinary tag stays legitimate.
  Existing violations are not converted: the 4 group tags on 65 photos stay as tags and drop out of `photo_people` by the rebuild.

#### Names to review *(#985; owner, 2026-10-09)*
A name on a face or in a photo's people that is no person's node is **not unnamed, tagged or created by the migration or by any
background step**. It is listed, per library, for the owner to settle one at a time.
- **Where it lives: a derived view, plus a tiny table of what was set aside.** The list is `person_ids.unresolved`'s pairs
  (`GROUP BY name, tag_id` over `idx_faces_person`, which stage 1 measured at 1.6 s on a cold copy of photo_index, faster warm; `photo_people` is a scan of 80,000 small rows), read when the
  page asks and never cached. Reasons: `none` (no person tag), `several` (two or more tags have the leaf; after stage 2 this
  means a *text* that cannot say which), and nothing for `branch` (a group tag is not a name; #986). The one table is
  `name_review_dismissals(name_key PRIMARY KEY, rows_seen, decided)` (migration 28, empty): a dismissed name stays hidden until it holds more
  rows than when it was set aside.
- **An entry** shows the name, the reason, the faces (with a few crops) and the photos that list it from a keyword alone, with a link to
  open them, and the person tags it could mean (every person with that leaf; for a name no tag has, the people with a close spelling).
- **Choices** (each a journaled change of its own, said and counted before it is applied, never run on open):
  1. *Make a person tag*: pick the group (a picker of the face roots and groups; one root offers itself, never silently chosen when
     several) and the node is made as a person under it; the name's faces and list entries take its id.
  2. *Link the name to a person*: pick an existing person (labelled with the group); the name's faces take that id and the person's
     name. For a name that came from another library's keyword the photos' *files* keep their old keyword; the entry says how many
     photos that is and offers the existing tag Merge (`tags.merge`) for them, which writes the files, as the owner's own step.
  3. *Unname the faces*: the faces become unnamed (`faces.unname`, manual source as the face panel's Unname writes it); photo
     keywords are untouched.
  4. *Dismiss*: keep as is, hidden until new rows arrive.
- **Where the owner opens it.** TagTuner, Review People: a first row "Names to review (5)" above the people (shown only when the count
  is above 0), opening a dialog with the entries. Activity shows the count with a link to it; the doctor and the MCP's checks report
  the count (names by `--show` only). A route pair in `tuner_routes` (`GET /api/names-to-review`, `POST /api/names-to-review/resolve`)
  over `tagpup.services.name_review`, which uses `person_ids` and `face_people`/`tags` for the writes and holds no SQL of its own.
- **A second kind, once a read made it: a new person with a name another person has.** A file read that makes a person tag whose leaf
  another person already has (a stale keyword of a moved person; a photo copied from another library) is *not* the same person
  until the owner says so. The list shows it too ("Sam, Family/Ingersoll, no faces: the same person as Sam, Family/Thackeray?
  Merge / keep both"). Later phase.

#### Showing the group when a leaf is shared *(the owner's requirement, 2026-10-09)*
**One decision in Python, one string in JS, one fixture holding them together**, as `person_tag` / `resolveTagOrPerson` are held by
`tests/fixtures/person_filing.json`.
- `tagpup.core.vocabulary.person_labels(nodes)` takes every person leaf of the library as `(id, tag)` and returns, for each, `shared` (another
  person leaf has the same `key`) and `group`: **the shortest tail of the parent path that tells the sharing people apart** (one segment,
  as `Thackeray`, when it is enough; two when two groups end alike; the whole parent when needed), empty when not shared. It is computed over **all** the
  library's people, not over the list being shown, so a filtered list of one Sam still says which Sam. Services answer people as
  `{id, name, tag, group, shared}`; nobody else computes `shared`.
- `web/common/vocabulary.js` gets `personLabel(person)`: `name` when not `shared`, else `name + ' · ' + group` (a middle dot, shown
  `Sam · Thackeray`), and `personTitle(person)` (the full tag, for the hover title and the screen reader). No page builds that
  string, joins `group`, or compares two people by their label; they compare ids. The fixture `tests/fixtures/person_labels.json`
  (libraries of people with the labels expected) is read by `tests/test_person_labels.py` and `tests/frontend/person-labels.test.mjs`.
- **The form** is `Sam · Thackeray` (leaf first, because lists are sorted by leaf and the leaf is what is scanned for; the group is dimmed),
  not `Family/Thackeray/Sam`. A narrow window cuts the group at its END (`Sam · Thackeray/Cou…`) and never the leaf -- the group is already the shortest tail that tells the sharing people apart, so its START is the discriminator and cutting it from the left made two cousins read alike (#1083); the full
  path is the element's `title` and `aria-label`. Sorting is by leaf, then by group, so two Sams sit together.
- **Surfaces** (each reads a person item, none a bare name): TagPup's face panel (the name box's offered people, the five-nearest
  buttons, "named elsewhere"), the photo's people chips and the selection panel's people, Suggest's chips (a name-only suggestion that
  two people match is offered as two labelled chips, replacing today's "which folder?" question), the tag editor's person suggestions,
  the navigator's people list (a tree: the group is already its parent row; a flat list and a filter result get the label), the search
  chips and results header, the person-faces hover (`attachPersonFaces(button, person)`, keyed by id), TagTuner's Identify Faces
  assign popup and bulk "Name as..." confirmation and undo toast, Review People's list and its header, the new-person dialog (see
  below), the rename dialog, and the CLI's and MCP's revealed output.
- **New person.** Typing a name no person has makes a person, under the group the typed text names (`Family/Thackeray/Sam`) or, for a plain
  name, today's rule (the one face root; asked when several). The dialog gains an optional **Group** box (a picker of the face roots and
  the groups under them). A new person whose leaf another person has is made without a warning screen; it is simply shown as `Sam · Group`
  afterwards, and a dialog line says "another person is called Sam (Family/Ingersoll)" so a typing slip is seen before it is made.

#### The journal, and history from before ids
- `tag_id` leaves `journal.DERIVED_COLUMNS`: a change records a face's `tag_id` and `name` both. A row's precondition ("the rows are what the change
  expects") compares `tag_id`, and `name` **only when the recorded `tag_id` is NULL on both sides** (an unresolved name), so a leaf rename
  between a naming and its undo no longer makes the undo a conflict. The cache refresh a rename makes is a derived write, not a change of the face.
- **Entries recorded before the build stay undoable, translated lazily.** A change whose `schema_version` is below the migration is replayed
  as stage 1 replays it today: the recorded `name` is put back and `person_ids.follow_faces` gives it the id the tree gives that name *now*
  (`_derive`); a name that no longer resolves leaves `tag_id` NULL, an unresolved name that the review list shows. Nothing in a journal is
  rewritten (the journal's tests compare it byte for byte), and the rehearsal compares after `_derive` on both sides, as now.
  Counted: no library holds a `faces` row in its journal, so today this is a rule with nothing to run on.
- Tree rows keep their ids in the journal (686 `change_rows` on photo_index, one change), so the tree's undo needs no translation.

#### Migration and phases
Migration 28 is **additive**: `idx_faces_tag` on `faces(tag_id)`, `idx_photo_people_tag` on `photo_people(tag_id, photo_id)` and the empty
`name_review_dismissals`; the trigger comes with phase 5 as a migration of its own, also additive. No row of the libraries is written; stage 1 already filled both `tag_id` columns, and they are
what the names give today. **Measured before it is built, on a sandbox copy**, as migration 21 was (that one took 17.5 s cold; an index over
226,208 faces is expected in seconds; it runs where 21 does, at the server's start, in the background thread, never on a page's request).
The data that changes is derived and is rebuilt by the owner's command, never on open: `tools/doctor.py --rebuild-derived --apply` after
a rehearsal that counts the photos: photo_index at most 77 (the 12 whose shared-name keyword now gets its exact id, the 65 whose group tag stops being
a person), the others none. Until it runs those photos list their people as before and the doctor's `people_out_of_date` says how many.
**An app from before the migration must not write the library after it** (it would write names without ids and the turned-round doctor
would then overwrite them): `schema.ensure` refuses a library whose version is above `LATEST` (today it does not: it only opens), with a message that says
which app to start. That is a new guard (question 7).

| Phase | What | Size |
|---|---|---|
| 1 | **Built (part A).** The group tag is not a person (#986): `PeopleVocabulary` is given the tree's groups (`taxonomy.group_tags`), `extract_people` drops a group's keyword (path or bare leaf), `people_out_of_date` agrees, `tools/doctor.py --rebuild-derived` rebuilds the photos' people (`people.repair`; it did not before). Not done here, left for phase 4 with the references: `PeopleVocabulary` rows carrying ids, `people_in_photo` returning references and deduping by id. No schema. | M |
| 2 | **Built (part A).** Migration 28 (`idx_faces_tag`, `idx_photo_people_tag`, the empty `name_review_dismissals`), and the newer-library guard (`schema.NewerLibrary`, every entry point), measured on a copy. | S |
| 3 | **Built (part A).** `person_labels` + `personLabel` + the fixture, and a `person` `{id, name, tag, group, shared}` on the answers that name someone (additive; pages unchanged; the list is in SPEC_TAGTUNER.md, `/api/people?records=1`). | M |
| 4 | **Built (part B).** Writers and readers move to ids: `faces.name_as`, `person_ids.resolve`, the 158 reader lines, `counts_by_person`, identify/automatch/clustering known sets keyed by id, the wire's `person_id`; `person_ids` follows turned round; tests with two Sams in the fixture. | L |
| 5 | **Built (part B; with 4).** (Was: until it exists, a tree edit that gives a person a child (a tag under them) silently changes the people of every photo carrying them (they become a group and leave the photos' lists, N photos, by the part A rule); the refusals of rule (a) are this phase.) Tree operations by id: rename of one node, move, merge, delete with refusal and force, the trigger on, the two rules as refusals, journal recording of `tag_id`, the lazy rule for old entries. | M |
| 6 | **Built (part C).** The pickers show the group on every surface listed (`personLabel`; the group's span is cut at its end, never its start); the pages hold the people as a `PeopleDirectory` and send `person_id`; a name two people have asks which; the new-person Group box. | M |
| 7 | **Built (part C).** Names to review: service, routes, Review People row and dialog, Activity, doctor and MCP counts, dismissals. *Not built:* the "new person with a shared name" entry (the second kind, "Later phase" above). | M |
| 8 | **Built (part C).** Cleanup and docs: the page's `personExists` (leaf and name lists) is gone, the design marked built, DATABASE and the three SPEC documents updated. The bare-name wire (CLI, MCP, a page not reloaded) is kept, as the design says. Columns are not dropped. | S |
Phases 1 and 3 can start at once and are worth having first (1 is an owner decision already made; 3 changes no behaviour). 4 and 5 must not
be split across a merge: the id is the key only when every writer writes it. Sizes are worker rounds: S about half a day, M one to two days, L three.

**Part A as built** *(2026-10-09; branch worktree-agent-aae3c5e71aecc6882)*, and what was decided in building it:
- **The group tag.** A face node with a node under it is a *group* (`taxonomy.group_tags`, the same reading as `person_ids.People`'s branches).
  `PeopleVocabulary.from_rows` is given them: a group's keyword -- spelled as a path, with a backslash or a bare leaf -- names nobody, and is in
  no `by_keyword`, so `extract_people`, `face_people._person_tags` (faces-from-tags), `people_paths` and `people_filing` (where a name is filed,
  hence what a click on a chip writes) all drop it by the one rule. A bare keyword that equals a group's
  leaf still names a *person* of that name elsewhere. `people._touched` compares the groups too, so a person given a tag under them leaves their
  photos' lists in the same transaction (`tree_edit`). **A face's name and a person field of the metadata are names, not tags, and stay listed**
  even when they spell a group (none exists on photo_index: the 7 faces were unnamed on 2026-10-09; they would be the review list's, #985).
  Counted read-only on 2026-10-09: photo_index 9 face groups, 4 carried by photos, **65 photos** whose people the rule now changes
  (`people.stale`, 10.8 s); kr-track and renton_parkrun none. The 12 photos of the shared-leaf keyword are phase 4's (ids), not 65 + 12 yet.
  *The doctor's `--rebuild-derived` did not rebuild `photo_people` at all*, only the derived tables and the ids: it does now (a dry run counts the
  photos; `--apply` rebuilds those photos only, outside the write lock for the reading).
- **Migration 28** is `idx_faces_tag` on `faces(tag_id)`, `idx_photo_people_tag` on `photo_people(tag_id, photo_id)` and the empty
  `name_review_dismissals`; additive, `touches` is the new table alone (so `schema_gap_blocker` is unchanged and a change made at 27 is undoable
  after 28; no `faces` quick_check is run, which migration 21's cost 6.5 s). **Measured** on a copy of photo_index (2,615 MB, copied by
  `db.copy_database` into a home of its own, then deleted): the copy was at schema 26 (the live library has not had 27 yet), 27 and 28 together
  took 0.8 s of which **28 is 0.6 s**; the planner then answers `WHERE tag_id = ?` as `SEARCH ... USING COVERING INDEX idx_faces_tag` and
  `idx_photo_people_tag`. Interrupted, it leaves 27 and runs again; two processes apply it once (both tested).
- **The newer-library guard.** `schema.ensure` raises `schema.NewerLibrary` (its text names the library by file name, its schema and this app's, and
  says to start the newest TagPup) for a version above `LATEST`, before it writes anything, so the journal, settings, snapshots and every store
  function that opens through it refuse too. Reads through `readonly_uri` do not pass `ensure`, so each entry point also asks `schema.newer_problem`
  (read-only; a file it cannot read is not called newer): the server's library middleware answers **409** with the sentence (JSON `{success, error}`
  under `/api/`, plain text for a page) for a library under its address, `/api/server` -- which a hand-over waits for -- still answers; the picker's
  Create answers 400; the CLI's group refuses before any writing command (exit 1); the MCP's writing tools raise a ToolError; the doctor's
  `--rebuild-derived` exits with the sentence. The sentence names where the backups are (data/backups, the snapshots under it) and how to go back.
  **Recovery is not blocked** *(review round 1)*: an older checkout is how the owner goes back, so ONE owner, `schema.reading_newer()`, says which
  operations may open a newer library, as it is, with a one-line note and nothing migrated, settled or written: the CLI's `history` and
  `snapshots` (list and restore; `schema.RECOVERY_COMMANDS`), the doctor's report, the MCP's read tools (their answers carry a `note`), and the
  server's `/api/server`. Everything that writes through the app stays outside it and refused. **When it first bites:** not for the app
  installed today -- its `_ensure` is `if version >= LATEST: return` and it opens a 28 library like any -- but for an app that has this
  guard, that is from the update after this one is installed. Until then the owner must not start an older checkout against a migrated
  library. **What it cannot do:** the hand-over starts the old version again when the new one does not answer; if the new one had migrated
  the library by then, an old version that has the guard is refused until the new one is started (a rollback of the code needs a snapshot restored).
- **The labels.** `vocabulary.person_labels` is one decision over every person of the library; the same *length* of tail for each person who
  shares a leaf; groups differing only in case fall back to the whole parent. `Directory` (`person_ids`) tells a person to the pages and is read
  from the tree inside the answer, never kept. **Decision:** the fields are one nested `person` on each carrying record, not flat beside `name`, since the
  navigator's people already have a `group` of their own (the branch they are filed in) and the tally's `tag` means a tag. A row keyed by a
  name two people share has `person: {id: null, tag: null, group: "", shared: true}`, since part A cannot say which; part B keys the rows by id.
  The photos' `people` lists of names and the dicts keyed by name get nothing now (a card list of thousands would carry a parallel array for
  every photo): the page keeps `/api/people?records=1`, where a name is looked up. The cached Identify queue and the saved suggestions are
  labelled when they are served, never inside the cache.

**Part B as built** *(2026-10-09; branch worktree-agent-a7c53f38d987029d9; phases 4 and 5 together)*, and what was decided in building it:
- **The id is the key; the name is its cache.** `faces.tag_id` and `photo_people.tag_id` hold the node; `name` is the node's leaf, kept in step by
  every writer and by the tree operations (`person_ids.follow_faces`, `follow_listed`, `follow_tree`; `out_of_step(spelling=)` finds a cache
  that differs; `fill` is migration 21's one-time backfill and nothing else). A row with a name and no id is an *unresolved name*
  (the review list's, phase 7). **One door:** `person_ids.target` / `resolve` turn what a caller sends -- an id, a tag path or a name -- into one
  Person or a refusal (`StalePerson` 404, `AmbiguousPerson` and `GroupNotPerson` 400, the sentence naming the candidates' tags);
  `People.settle` drops the id of a node that is gone and keeps the cache; **it gives a name with no id none**, however unique the name has become
  (round 2). **One owner links an unresolved name: `person_ids.link_added`**, for a name a person was ADDED under (`follow_tree` after an edit
  of the tree; the journal for a node its change inserted or renamed). Otherwise a writer that knows the id writes it, a keyword is resolved by
  its path, and the owner links the rest (the names to review).
- **Writers** (`store/faces.py`): `name`, `name_if_unnamed`, `name_unnamed`, `set_names`, `reinstate`, `revert_automatic`, `unname*`, `exclude`, `insert`,
  `unname_person`, `rename_unresolved` write the id with the name in one statement; a bare name two people have is refused, never guessed
  (a detected face given such a name stays unnamed). The keyword writer no longer drops "a person the file already names by their leaf": two
  people called alike are two keywords (`vocabulary.names_same_person`, by the id of their nodes) -- *before part B the second Sam's tag was
  skipped*. **Readers** return `Ref(id, name)`: `people_in_photo`, `people_by_face`, `counts_by_person`, `face_refs`, the matrices, `KnownFaces.labels`;
  a card, a count or a row of the pages keeps its name and gains `person` / `person_id`. Clustering stays name-based (a shared name's faces are
  compared as one person there: a decision, not a defect; the faces still carry their ids).
- **The wire.** `person_id` is preferred in every request that names a person (`face/match`, `faces/match-bulk`, `person/rename`, `person-faces`,
  `unmatched-faces/person-matches` and `build-status`, a bulk People edit's `add_ids` / `remove_ids`, the plans of the assignment job); a name still works
  when it is one person's (an open old page, the CLI, the MCP). `/api/people-faces` and `people-face-samples` are keyed by name when nobody else
  is called alike and by `id:<id>` for every person with a node; the queue rows carry `person_id`. `inspect.photos(person=)` takes a tag path
  (that person) or a name (everyone called it).
- **Tree operations by id** (`store/taxonomy.py`, `services/tags.py`). *Rename* renames the one node picked, never joins two people and never merges
  into one who is there (refused, "merge them instead"); the faces and lists follow in the same transaction, the photos' files after (the journal's
  resumable change). *Move* between groups keeps the id. *Merge* (`move_branch` onto an existing node) repoints the faces and lists to the target
  before the node goes, and the plan says how many photos would list one person twice and how many have a face of each (owner's question 6: allowed).
  *Delete* of a person faces name is refused with the count; `force` unnames those faces in the same transaction (they become faces to review, not
  decided as nobody) and then deletes. **The two rules.** (a) *no children under a person that faces or photos carry* -- for person tags only (owner's
  answer to question 1): refused for an owner's action (the tree view's add, a move under a person, the keyword writer's new tag); never for what a
  file holds (the indexer reads a path as it finds it). (b) *a group tag is never a person* (part A) is refused where an owner would set it.
- **Final round (2026-10-09).** Rename Person on a name no tag has, into a person's name, is a writer that knows the id and writes it
  (`faces.rename_unresolved`; a name two people have is refused). A name that is exactly one person's but whose rows are linked to nobody (a
  same-named person left) is REPORTED -- `person_ids.unresolved()` bucket `one`, the doctor, `checks.names_without_a_person` -- and linked
  only by the owner: `people link-name <name> [--apply]` (`services.people.link_name`; a dry run; one journaled change of the faces,
  undoable). The refusal of an undo (`_person_gone`) is gone with the fill it guarded against. The page action is the names to review's **Link to this person** (part C).
- **Review round 2 (2026-10-09).** The ownership question of round 1, answered: see "`person_ids.link_added`" above. `settle` filled any
  unresolved name that was unique NOW, so a rename of one of two same-named people and then any rebuild of the photo, face write or journal
  replay linked a hand-decided face to the other; it no longer fills. `person_ids.repair` and the doctor put names and dead ids right and
  link nothing; a journal undo of an entry that recorded only a name leaves an unresolved name; fixtures state who a face is. A tree edit takes
  the write lock BEFORE it reads the tree (`people.tree_edit`: BEGIN IMMEDIATE), so two processes cannot decide from a stale tree
  (`tests/test_people_by_id_at_once.py` holds one transaction open until the other is at its edit). The undo of a force delete or a merge returns the
  faces as unresolved names (the name and the decision back, the dead id dropped), never to another person called alike, because nothing links
  an unresolved name by itself; History says what an undo does not give back (the person, the photos' keywords). Cost, measured on a copy of photo_index: the biggest person (7,468
  faces) takes 8.0 s to force delete and writes 22,404 change_rows (the whole journal held 17,053); the rehearsal that refuses a bad merge
  before any file is written takes 6.6 s for it. The refusal text and `usage` say the rows ("about N rows", `history_rows_if_forced`).
- **Review round 1 (2026-10-09), what changed.** (1) *One rule for linking an unresolved name after a tree edit*
  (`person_ids.follow_tree`): only the rows of a name NO ONE had before the edit and a person has after it (a node made, moved or renamed
  into it) are linked; a name that became one person's because a same-named node LEFT (rename away, merge, force delete) was ambiguous
  before and its rows -- a hand decision too -- stay unresolved for the review list. (2) *Every refusal of a merge or move comes before the
  first file is written* (`tags._tree_refusal` rehearses the tree edit in a rolled-back transaction, so the rule is the tree's own).
  (3) *Reads fall back, writes do not:* `person-faces`, `person-matches`, `build-status` and `people-faces` accept a name two people have
  and answer for all of them (`person_ids.SharedName`, `people.for_reading`; nothing is created or linked); a write with it is still a 400
  naming the candidates, and the page shows that text. (4) *The single owner of an undoable record of a face's identity change is the
  journal* (`journal.record_faces`): a force delete (`taxonomy.delete_branch` / `faces.unname_person`) and a merge (`people.merge_person`)
  write ONE change of the face rows (name, name_source, tag_id; counts only in its summary) in the same transaction as the tree edit, so
  History can put the faces back; an undo that would give a face whose person is gone to ANOTHER person called alike is refused. **The tree
  rows themselves are still not journaled** (as before part B; finding). (5) `faces_renamed` is counted after the write, a group counts the
  faces under it. (6) `removals.removed_people` carries the id the change recorded. (7) The navigator keys a person by id (`p:<id>`) and opens a
  shared name by its tag path. (8) Two real processes editing the tree: `tests/test_people_by_id_at_once.py`.
- **The trigger** `person_tag_not_deleted_while_named` (**migration 29**, additive, triggers only) aborts the DELETE of a node a face names on any
  connection; `generation_faces_update` now also fires on `tag_id`.
- **The journal.** A change records `faces.tag_id` and `photo_people.tag_id` as columns of their own (`journal.cache_columns`; `DERIVED_COLUMNS`
  no longer has `tag_id`); a change recorded before part B has none and is replayed by the name (`_named_by_name`: the person that name is
  now). The undo of a node's insert releases the faces linked to it (`person_ids.release`) and is refused when a decision of the owner's names
  the node. *Not done:* the design said tree edits are "journaled with the tree row"; they are not journaled today (the tree is not a journaled
  table), so there is nothing to record for them (finding below).
- **The stale-keyword case, as built.** A file that still holds a path no node has (a rename interrupted between the tree and the files, a copy from
  another library) is read as the name its path ends in; if that name is one person's now, the settle rule lists the photo under that person
  until its file is rewritten (the journal resumes the rename; tested). It is wrong for the moment and visible in the review list; the former-paths
  record (question 5) was not built.
- **The library views.** A person in a view is the node (`tag_id IN (...)`, `idx_photo_people_tag`); a *name* in a search or a view is every
  spelling the rows hold (a view of "Sam" shows both Sams, as it always showed the name), a tag path is exactly that person. The navigator's
  people are `(Ref, photos)`, two people called alike two entries each filed under its own branch; names with no node merge by spelling. The
  tally counts by node. *Counted on a copy of photo_index (2,615 MB; 895 tree nodes, 406 people, 9 groups, 1 leaf with two nodes):* `people_counts`
  8 ms, `people_groups` 54 ms, `counts_by_person` 3 ms, the whole tree read 2 ms, `out_of_step` 4 ms (faces) and 47 ms (photo_people),
  `person_ids.unresolved` 50 ms (4 names with no person, 1 with several, 4 that are a branch), a whole settle with nothing to change 54 ms,
  migrating 26 to 29 1.4 s, and **renaming a person 1.05 s**. Every lookup by id plans as `SEARCH ... USING COVERING INDEX idx_faces_tag` or
  `idx_photo_people_tag`; the only temp b-trees are the grouping of one pass over `photo_people` (as before) and the distinct unresolved names.
- **Left for part C** (done: see "Part C as built"): the pages' own leaf-based "already has" rules and duplicate checks, the pickers that send ids
  from every surface, the names-to-review service, and the shared-leaf specs of the navigator.

**Part C as built** *(2026-10-09; phases 6, 7 and 8; findings #1074-#1080)*, and what was decided in building it:
- **The names-to-review list** is `person_ids.review_pairs(conn)`: two grouped reads (`faces` by `idx_faces_person`, `SEARCH ... (name>?)`, and
  `photo_people` by `idx_photo_people_name`) of the names that hold NO id, one `Review` per name key (all its spellings together; `why` none,
  several, one or branch; faces, those decided by hand, listed people, those from a keyword), never stored. **Counted read-only on the live
  libraries this task (each read 0.03 s, and none has migration 28 yet, so none has a name set aside):** photo_index 9 names -- 4 with no person
  tag (80 rows: 40 faces, 27 of them decided by hand, and 40 listed), 1 with two (13 rows), 4 that are only a group (65 listed rows, no face) -- so **5 names wait** and
  the 4 group names are `stale_group_rows` (the rebuild drops them, #986); kr-track 12 names with no tag (186 faces, all decided by hand, and 188 listed); renton_parkrun none. The one
  rule of what waits is `store.name_review.split`: a group tag's listed-only rows are not a name to settle; a name set aside is hidden until it
  holds MORE rows than `rows_seen`; the service, the doctor and the MCP all use it.
- **The service** `tagpup.services.name_review` (`entries`, `count`, `resolve`): four choices, each a rehearsal unless `apply`, each ONE write
  that reads the name, the person and the group again under the write lock (`BEGIN IMMEDIATE`) -- a name settled in another window is refused in
  words, a person or group gone is a 404, two windows choosing at once leave one change, a failure in the middle rolls the tree row back with the
  rest (all tested). *Make* is `taxonomy.add_node` under the group (so rule (a) is `refuse_child_of_person`'s), and the tree edit's own
  `follow_tree` links the name -- the one writer of that link is `person_ids._link`, which `link_added` and the owner's `link_to` share. *Link*
  gives the name's faces and listed people the person's id and name (`link_to`; for a person called otherwise, `keywords_kept` says how many photos
  keep the old keyword in their files, and the existing tag Merge rewrites them). **Keyword rows are never rewritten by name** (review round 1,
  #1081): a listed row made by a keyword is its PATH's person, so `_link` and the owner's link/make touch `faces` and listed rows of source `face`
  only and then write the touched photos' lists again by the one rule (`faces.relist`); the rows a keyword made are counted apart
  (`keyword_photos`, not rows to settle -- the real pet and friend entry is 1 face and 12 such rows) and have their own choice, **Rebuild these
  photos** (`people.rebuild`; derived, not journaled, and said so). A result with no journaled change does not say "Undo it in History". *Unname* uses `faces.unname` with **name_source NULL, not
  "manual"** -- *a decision, not the design's text*: "manual" is "this is nobody", which Identify Faces never offers again, and the owner's
  intent is to identify them (a person's forced delete leaves its faces the same way). *Dismiss* / *Show again* write `name_review_dismissals`
  and are not journaled (a preference; nothing of a face or photo changes). Journal operations: `PERSON_MADE`, `PERSON_LINKED` (the CLI's `people
  link-name` writes the same), `NAME_UNNAMED`, each one change of the faces with the undo's note in History.
- **The routes** (`web/name_review_routes.py`, both apps, this PC only): `GET /api/names-to-review` (`?count=1`, `?dismissed=1`) and `POST
  /api/names-to-review/resolve`; 409 while the faces are clustered or named from tags, as every write of faces. The count is on the Activity page's
  attention answer (`names_to_review`, `names_url`), in `tools/doctor.py` ("names to review: N waiting, M set aside") and in the MCP's `checks`
  (`names_to_review`: counts by reason; a name only with `reveal`).
- **The pages.** `web/common/vocabulary.js` holds the one `PeopleDirectory` (`/api/people?records=1`: a tag path names exactly one person, a bare name
  one only when nobody else has it, a label names the person it shows), `sameRecord`, `sameTagPerson`, `sameNamed`, `personFields` (the id when
  there is one) and `photoAlreadyHas(photo, tag, isPersonTag, directory)`; with no people read, a library with no person tags, they are the old
  leaf rules -- the one fallback kept, since a leaf is all such a library has. `person-choice.js` asks "which one?" and never takes the first.
  Surfaces changed: TagTuner's Review People and Identify lists (rows by `person_id`, label, search by label, `person_id` in the address), the
  Assign box / cluster guess / Assign Cluster popup / face card Match / badges (a badge holds the LABEL, which finds its person again), rename,
  the faces strip and details, the New Person Group box; TagPup's face boxes, panel, strip, Suggest chips (a name two people have is two
  chips), the add-person list, the in-page and library tallies and their pill edits (`add_ids` / `remove_ids`), the navigator, the search's
  chips and a view's words, and the person-faces hover (by `id:<id>`). The label as an element (`person-label.js`) shortens the GROUP from its
  left with CSS (`direction: rtl` on the group's span) and never the name.
- **Decisions the design did not state.** (1) New Person with a group makes the tag first through the tag editor's create and names the faces by
  the id it answers with (two requests; if the second fails the dialog says the person was made) -- the assignment job's plan carries a name, and
  teaching it a group would have been a larger change of the job than the owner asked for. (2) A name no tag has but the faces hold is "exists", not
  new, in the pickers (hidden people stay people). (3) The in-page tally keys a person by id and the suggestion tally by tag only for a person who
  shares a name, so an unshared person's bare-leaf write is unchanged. (4) Names to review is Review People's, as decided; the Identify queue has no
  such row.
- **Not done, and why.** The second kind of entry ("a new person with a name another person has", later phase). `other_names` in Identify's "also
  names X" notes and a photo details' `people` (names, no ids) are names the server sends without ids: they cannot say which Sam. The History
  dialog never shows a person's name, so there was nothing to label. A leaf-keyed `/api/people-faces` entry is still sent for a person with a node when
  nobody shares the name, for an open page from before the update.

#### How it fails (each is a test before it is built)
- **Interrupted.** The migration is additive and one transaction: a crash leaves 27 and the next start runs it again. A tree operation is one
  transaction for the database (a crash keeps the old or the new, never faces with a name no node has); its file rewrite is the journal's
  resumable change, so an interrupted rename leaves some photos holding the old path. Those photos are the **stale-keyword case** below.
  The names-to-review writes are one change each; interrupted, they are in the journal as incomplete and settle at start like every change.
- **Two at once.** The always-on server and a CLI run: every operation above runs under the library's write lock inside `db.write_with_connection`,
  reads the tree inside the transaction and never caches it across transactions (stage 1's rule); `resolve` is called inside the writer's
  transaction. Two libraries sharing a folder: ids are per library and a keyword is a path, so a move written to the files by library A reaches
  B as a changed file whose keyword path B's tree does not have (the stale-keyword case, for B).
- **The stale-keyword case** (a file edited elsewhere, a copy from before a move, a rename interrupted, a shared folder). The old path has no node, so
  reading it makes a *new person tag* with a new id and the same leaf: a second Sam. This is correct as far as the data goes (a path is a
  person) and is not silent: the second kind of review entry shows it. Whether to *prevent* it with a record of former paths is question 5.
- **A read that fails.** A library that cannot be read answers an error and decides nothing (`present` and the doctor already do). A stale
  `person_id` is a 404, never a new person. A tree read that fails in the middle of a rename aborts the transaction.
- **The real data's shape.** One shared leaf, 5 + 12 names to review, 77 photos to rebuild, 37 ordinary tags with children (so a rule (a) over
  all tags would be broken at once). The tests use two Sams under two roots, a person and a pet of one name, a group tag on a photo, a
  keyword path of a shared leaf, a name with no node, a name two nodes have, and a leaf rename between a naming and its undo.
- **What the owner sees.** A picker that answers from the page's cache (the people list) with `shared` and `group` already there, so nothing is
  asked per keystroke; the review list reads two pair queries (the cold read of faces' pairs on photo_index was 1.6 s in stage 1; warm and the second table to be measured on a copy) and
  says "Reading the library's names..." while it does.

#### Questions only the owner can answer
1. **Rule (a), how wide?** "A tag that photos carry gets no children" over *all* tags is broken today by 37 tags carried by 10,864 photos
   in photo_index (a photo holds `Places` and `Places/Seattle`) and would refuse ordinary keywording. Recommended: **person tags only** (a
   person that faces or photos carry), which only the 4 group tags (65 photos) already break.
2. **Ids on the wire between the page and the server** (never in a file): recommended yes; the alternative, a tag path in every request, breaks when
   a person is renamed in another tab.
3. **The four names with no person tag** were to be created (2026-10-07) and are, since #985, to be settled by hand from the list. Recommended: all of
   them, and kr-track's 12, by hand; nothing is made or unnamed by the migration or a background step. (The one name on two tags: link to one of them.)
4. **The label**: `Sam · Thackeray` with the shortest distinguishing tail of the group, full path on hover, shown only when a leaf is shared.
   The alternative is the whole path always when shared. Recommended: the short form.
5. **A record of former paths** (`person_former_paths`: a moved or renamed person's old path, so a stale keyword resolves to the person and a
   photo is not given a second Sam; additive, people only). It adds a meaning (a keyword with no node can name a person). Recommended: **not now**; the
   stale keyword case is shown in the review list (phase 7) and built only if it happens, since zero shared leaves are stale today.
6. **A merge of two people who each have a face in one photo** leaves two faces named one person. Recommended: allow, and say the count in the plan.
7. **A guard against an older app writing a migrated library**: refuse to open a library of a newer version (new behaviour of `schema.ensure`, so the
   installed copy and the CLI must be installed before the migration runs). Recommended: yes.
8. **Rename Person means one person** (the one picked), it never joins two people of the same name and never merges into an existing tag; merging is
   its own action. Changes what "Rename Person" does today. Recommended: yes.
9. **A dismissed name** comes back when it gains rows. Recommended: yes.
10. **Where the list opens**: Review People (a first row, shown only when there is something) with the count in Activity. Recommended as written.

**Decided (owner, 2026-10-09): every recommendation above, as written.** The owner answered 1 (person tags only), 4 (the short label), 8
(Rename Person means the one picked) and 7 (refuse a library newer than the app) directly, and accepted the recommended answers to 2, 3, 5, 6, 9
and 10 by not changing them. Build order: part A = phases 1-3 (the group tag is not a person; migration 28 with the newer-library
guard; the labels and their shared fixture), part B = phases 4 and 5 together (writers and readers to ids; tree operations, refusals, trigger and
journal), part C = phases 6-8 (the pickers show the group; the names-to-review list; cleanup). Each part is reviewed and merged before the next.
