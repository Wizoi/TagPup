# Phase 6 design: bake vectors and face regions into the photo files

Status: DRAFT 2026-10-24, from the three owners' read-only sweeps (Files, People, Integration). Nothing is built; no
file is written. Parent plan: [AI_PIPELINE.md](AI_PIPELINE.md) "Phase 6". Counts below were made this session.

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
(compatible read, stale queued); share asleep mid-backfill; bake, then an app keyword write, then an Explorer edit
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
