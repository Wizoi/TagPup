# Phase 6 design: bake vectors and face regions into the photo files

Status: DRAFT 2026-10-24, from the three owners' read-only sweeps (Files, People, Integration). Nothing is built; no
file is written. Parent plan: [AI_PIPELINE.md](AI_PIPELINE.md) "Phase 6". Counts below were made this session.

> **Retired by cut 3 (2026-10-27, docs/PLAN_KEEP_IN_STEP.md):** `bake-photos` and `face-pass` are gone; the index keeps the files and the library in step on its own. The `bake-photos` command this design names is deleted; the index writes the bake (cut 2).

## 1. Live shape

| Library | Photos | Faces | Extensions |
|---|---|---|---|
| photo_index | 59,519 | 182,779 | 59,482 jpg, 19 jpeg, 17 tif, 1 png |
| kr-track | 1,499 | 11,457 | jpg |
| renton_parkrun | 1,309 | 3,644 | 1,308 jpg, 1 png |

Every library's photo vectors are under one SigLIP 2 key. Schema 33, no migration in flight. TagPup copies nothing
between machines; the owner copies a folder by hand and the library finds it through sync.

## 2. What exists and what does not

- **Reusable:** `file_changes.write_fields` (journal, `follow_fields`, `embeddings.restamp`, `settle_once`), the
  `ExifToolSession`, `services/faces.record_batch` (the one way detections become face rows, and it already runs
  naming-from-tags), the CLI shape of `embed-photos` / `face-pass` (`--apply`, snapshot, `gpu_turn`, stop after this
  photo).
- **Missing, Files:** a custom XMP namespace (ExifTool needs a `-config` file loaded at process start; nothing sets one);
  any region read or write (mwg-rs `RegionList` is a struct, and `field_values.write` and the journal's value model hold
  text lists only); Extended XMP handling (none; ExifTool does it for JPEG, some programs drop it); a picture
  fingerprint (only (mtime, size) stamps exist, which a bake defeats by its own write); a restamp for `face_embeddings`
  and `faces_detected` (a bake's own write would make the detector look again).
- **Missing, read side:** the indexer asks `embeddings.cached` for the photo vector only; faces have no cache lookup, so
  the hook goes in `begin` / `read` (`tagpup_cli.py`), skipping the decode when the bake's key equals the library's.

## 3. Shape (proposed)

1. **Format.** Per photo: a custom-namespace text value holding the model keys, the picture fingerprint, the photo
   vector and, per face, region + 512-d vector + a "looked, found N" count (a zero-face photo bakes "none found").
   Regions in MWG form (display-oriented, `AppliedToDimensions`) so other programs can read them; vectors in the custom
   namespace, because MWG has no place for them.
2. **Read side first**, against hand-made files: a bake is used only if its model key equals the library's current key
   and its fingerprint matches the picture; otherwise compute, or queue (no card). It is one more producer of `detected`
   dicts for `record_batch`, never a new writer. Names, tag ids, `name_source` and `excluded` are never baked.
3. **Write side** is a CLI command like `embed-photos`; dry run by default, snapshot first, `--apply`.
4. **Fingerprint** = a hash of the upright decode (`images.opened(upright=True)`), computed at index (the decode is
   already paid). A hash from bytes would not survive a metadata-only write.
5. **Census first** (read-only): one `ExifToolSession`, a stratified 1,000 of photo_index and all 2,808 of the other two,
   counts only: MWG / MP regions present, XMP size histogram (over 48 KB, over 64 KB), `HasExtendedXMP`, two XMP blocks,
   by extension.

## 4. Cuts (each under ~1,200 lines, each leaves the trunk whole)

1. Census script + read side against hand-made files, fingerprint (~500).
2. Write side on a sandbox copy, dry run only; restamp for face tables (~600).
3. `--apply`, Ctrl+C, settle at next start, `launcher.BEFORE_UPDATING` sentence, Activity row (~400).
4. The no-card queue, only if wanted.

## 5. Scenario tests that own the flow

Backfill interrupted at 30,000 and resumed; one folder baked on the desktop, copied, indexed on a no-card machine
(compatible read; a stale bake is worked out and counted by the machine that indexes it, no queue: #1918); share asleep mid-backfill; bake, then an app keyword write, then an Explorer edit
(restamp keeps it, fingerprint rejects it); an update installed while a bake runs.

## 6. Decided by the owner, 2026-10-24 (to be copied into DECISIONS.md)

1. **Where:** bake on the desktop, then copy by hand; the server's library reads the bake with no card.
2. **Backfill of the existing master:** TagPup writes the master over the share (not the desktop copy then robocopy).
3. **Undo:** journaled and undoable; no exception to the journal rule.
4. **Granularity:** one journaled change per folder (as face-pass).
5. **Migration 34 opens now** (a baked key / fingerprint column or bake-state table), in cut 1; one migration in flight.
6. **Formats:** all of them (jpg, jpeg, tif, png): 17 TIFF and 2 PNG are baked too.
7. **Per face:** box + vector only (no landmarks, norm or prob); a zero-face photo bakes "looked, none found".
8. **Excluded faces:** every detection is baked; exclusion stays per library. DECISIONS 2026-10-07's "an excluded one
   removed" is retired.
9. **Coordinates:** upright (MWG, with AppliedToDimensions), converted to and from stored pixels at the file edge only.
10. **Namespace:** MWG only.
11. **Fingerprint:** hash of the upright decode.
12. **Backup:** pilot on a few hundred photos with a separate copy; the owner confirms a current master backup (TagPup
    checks the named folder is recent, makes none); the run stops at once if the share stops answering.

Tensions to settle in the cut-1 plan: Q2 writes the master directly while Q1 has the desktop bake first (the backfill
is the one writer of existing masters; new folders are baked before the copy); Q5 opens the migration before the write
side exists, so its column must be the read side's, not a guess for cut 3.

## 7. Cut 1, decided 2026-10-24 (owner: "Approved" to the recommendations)

- Vectors are **float16**, little-endian, base64, one XMP value per face (each under ~64 KB), never one blob.
- Fingerprint = sha256 of the upright RGB decode, key `upright-sha256-1`; **Pillow is pinned** in requirements.txt.
- The read side decodes every photo to verify the fingerprint (the card is skipped, the decode is not).
- Namespace `XMP-tagpup`, URI `http://ns.tagpup.app/bake/1.0/`; tags PhotoModel, FaceModel, Fingerprint, PhotoVector,
  FacesFound, FaceVectors; regions in `XMP-mwg-rs`. A TIFF counts as orientation 1.
- **Migration 34** = table `photo_fingerprints (photo_id PRIMARY KEY, key TEXT, hash BLOB)`, additive, unjournaled, no
  FK; `services/rotation.py` clears the row beside `embeddings.forget`. No doctor check (as `faces_detected`).
- Branches: 1a census (~300), 1b orientation + fingerprint + parse + migration 34 (~400), 1c read side (~500).

## 8. Decided 2026-10-24 after the cut-1 reviews and the census (findings #1859, #1866)

- **Storage stays float16 vectors and MWG regions** (owner: yes). Measured: a face costs about 1,760 bytes (1,368 of them
  the base64 vector, 356 the region markup); int8 would save 40% but moves 36, 11 and 6 of ~1.1 million face pairs across
  the 0.35, 0.5 and 0.6 lines, so a baked vector would differ slightly from a computed one; int4 is out. One value per
  face; packing them into one value saves nothing.
- **Many faces: accept Extended XMP.** With 60 faces ExifTool keeps the regions and the photo vector in the main segment
  and moves only the face vectors to Extended XMP, so other programs still see the boxes; a 412-face photo adds ~725 KB.
  The read side must be tested on a bake that spans Extended XMP (cut 1c).
- **Other programs' regions.** The writer replaces `mwg-rs` regions and writes a photo with no faces without `RegionInfo`.
  Microsoft Photo regions (`MP:RegionInfoMP`) are left in the file and not read (owner: not used). Census, 2026-10-24
  (photo_index 3,000 of 59,519; the other two whole): MWG regions 4 / 0 / 0, MP regions 442 + 1 TIFF / 0 / 0, Extended XMP
  118 / 12 / 0, two XMP blocks 0, 1 unreadable in all; Orientation 6: 12 photos.

## 9. Cut 2 plan (write side, dry run only), from the Files, People and Data owners, 2026-10-25

- **Journal value model unchanged.** One field = one list of texts. The mwg-rs `RegionInfo` struct is one canonical text
  (`core/fields.py` `STRUCT_FIELDS`; `bake.region_text` and its inverse beside `bake._regions`; read with `-struct`,
  written as `-XMP-mwg-rs:RegionInfo=<text>`, empty clears it); `FaceVectors` joins `LIST_FIELDS`. `reads_same` needs no
  change. The writer's ExifToolSession opens with `config_file=` (the namespace config moves from `tests/baked_files.py`
  into `files/bake.py`; the test helper writes through `field_values.write`, so there is one writer). A dict reaching
  `write_fields` is silently planned as "clear" (`fields.field_values` drops it): the text must be a `str` first, guarded.
- **Builder** `tagpup/services/baking.py` reads store + files and writes only through `write_fields`: photo vector under
  `embeddings.model_key`, every face row holding a vector under the face key (excluded and "nobody" included; ordered by
  `faces.id`), boxes through `to_upright` + `area_of`, fingerprint of the upright decode; a pure `bake.values(...)`.
  The fingerprint row is put by a follower in `_followers` (`file_changes.py:527-531`) in the done transaction.
- **No restamp to build, no migration 35.** `follow_fields` keeps the photo row describing the file and restamps the photo
  vector; `faces_detected`'s stamp is kept and never compared; `face_embeddings` has none. Section 2's missing-restamp
  line is retired.
- **A stale bake** is found by parsing the file's own bake and comparing it with what would be written now (keys,
  fingerprint, regions): the file is the digest; no column.
- **Dry run writes nothing** (not a rolled-back write): no fingerprint row, no snapshot, no migration. CLI `bake-photos`
  (`@_holds_the_roots`, `--apply` refusing with one sentence this cut). It reports per library and folder: photos to bake,
  already current (skipped, so `--apply` resumes), cannot be baked and why, and by format (17 TIFF and 2 PNG). No byte
  estimate and no Extended XMP count (owner, 2026-10-25: ExifTool's detail): a bake measured 6,355 bytes of main packet
  plus about 1,712 a face, 35 faces spill FaceVectors whole into Extended XMP while the regions stay in the main packet
  (DECISIONS 2026-10-25); retired, not reported.
- **Branches:** 2a struct field kind, config into bake.py, the writer helper (~400); 2b the builder, the follower, the dry
  run CLI (~500); 2c scenario tests lifted into F3/F11 (~300).
- **Counts (2026-10-25):** photos with faces 49,111 / 1,485 / 1,285; with a `faces_detected` row 0 / 178 / 146 (all the old
  detector); with neither 10,408 / 12 / 18; faces with no vector under the current key 0 / 1,211 (532 photos) / 329 (244);
  journal text added by a full backfill about 499 MB / 24 MB / 10 MB, never pruned, copied by every snapshot.

### Decided 2026-10-25 (owner, cut 2)

1. **Journal:** the whole bake text is kept, undoable, about 499 MB in photo_index (and in every snapshot), never pruned; a
   retention rule is its own later cut if the size bites.
2. **Photos never examined under the face model** (10,408 / 12 / 18) are skipped and counted by the dry run; nothing claims
   "none found" for them.
3. **Old-detector faces in kr-track and renton_parkrun** (776 photos): retired on both before the bake (changes 347 and
   1225, counted 2026-10-25), so all three bake in one pass; the command is deleted (DECISIONS 2026-10-25).
4. **Dry run:** reads every file's XMP, decodes a sample of 200 photos per library to measure and to confirm the
   fingerprints, and reports "about N to bake"; the full decode is `--apply`'s.
5. **Migration 34** runs at the next app or CLI start; the dry run never migrates.
