"""Undo, from History, a change after a later change of another kind (scenario D2; #1805). Someone makes a change, then
a later one of another kind, then opens History and clicks Undo on the first. For every ordered pair of the kinds a
library really holds in sequence, each made by its real writer, the History dialog, the rehearsal and the undo agree
(tagpup.services.journal.why_not is their one account): the listing offers the Undo button exactly when the rehearsal and
the undo do not refuse; a refusal writes nothing -- no row, no generation, no change's status, no file; an undo allowed
is a round trip the rehearsal found exact; and whether it is allowed is what the owner was promised (EXPECTED_REFUSED),
saying so in the sentence of the check that owns it: a stamp, a migration and the switch to the new face model are never
undone (the last would delete the faces.model row it inserted: settings.NOT_FACENET, owner 2026-10-28; a switch back to FaceNet is refused at
its source, so no library holds one, scenario V11). A refusal that a later change of its own stands in the way of is
lifted by undoing that one first: undone in that order, both are.

The kinds: a stamp of the settings, a change of the settings, a switch of face model, a face pass (by its
writer: a kind the live journals hold, its command deleted), a migration's record (a synthetic additive step, tests/migration_names.py), a sync and a refresh of a row re-read from its
file, a sync removing the row of a photo whose file another program deleted (with its named face), Save on a photo, Smart Rename, a photo's name kept
before its first rename (Smart Rename's first change, alone), the sweep of a caption repeating its photo's title, a rotation (of the photo the later pass gave a face and its crop) and a bake of a
folder's photos (real ExifTool, changes of photo files; by its writer, as the face pass). The refresh's writer is
deleted (DECISIONS 2026-10-28, THE MCP SERVER ONLY READS; kr-track holds one, change 2): it is recorded as that writer
recorded it, a row re-read through refresh_rows.reread and edits_for on the maintenance scaffold. Every kind UNDO_CHECKS
names that the code makes is a row here (tests/undo_kinds.py, OPERATIONS) or is left out with its reason (LEFT): a kind
added to the journal fails tests/test_every_change_kind_has_an_undo_check.py in the fast tier until it is placed (#2748).

Lifted (each scenario a pair here, their tests deleted): test_history_routes' listing agreeing with the rehearsal; test_journal's migration never undone; test_journal_through_the_services' refused undo writing
nothing; the refusals of the face model's switch of P10 (now tests/test_a_library_indexes_and_suggests_with_its_face_model.py).

One library is built (harbour: seven photos read by the index, faces as the old detector and the new model's pass left
them, switched to the new model), copied after each earlier change, and the copy taken again for each later one, with
the photo files put back as the earlier change left them. Fictional names; a home of the test's own; rows as the
indexer and the pass store them.
"""
import contextlib
import os
import re
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import page_writes  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import face_rows  # noqa: E402
from face_rows import FACENET, before_the_face_model, person_of  # noqa: E402
from test_file_journal import Crash, crash_at  # noqa: E402
import baked_files  # noqa: E402
from undo_kinds import OPERATIONS  # noqa: E402

import numpy as np  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import bake, field_values  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import baking, file_changes, maintenance, refresh_rows, sync, tagging  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, embeddings, file_journal, generations, inspection, journal, schema  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
NEW = validation.SCRFD_ADAFACE


def vector(rng):
    found = rng.normal(size=512).astype(np.float32)
    return (found / np.linalg.norm(found)).tobytes()


# ---- the kinds, each by its real writer: make(case, library) -> the change's id, True (written, no change of its
# own), or None (the writer made nothing here: the pair is not one a library can hold) ------------------------------

def a_settings_change(case, library):
    changed = library_settings.change(library, {"candidates.tags": "Heron"})
    return changed.details.get("change") if changed.changed else None


def a_later_pass(case, library):
    """A pass under the new model that finds a face in the sixth photo: its own change, as the deleted pass recorded it."""
    path = case.photo[6]
    conn = db.connect(library.path)
    try:
        change_id = journal.record(conn, journal_service.FACE_PASS % NEW, [],
                                   {"folder": store_roots.to_row(conn, os.path.dirname(path)), "matched": 0, "new": 0})
        face_rows.as_a_face_pass_gave(conn, change_id, path, NEW, [],
                                      [([7, 7, 27, 27], 0.99, vector(np.random.default_rng(8)), bytes([7]) * 64)],
                                      {"matched": 0, "new": 1})
        conn.commit()
    finally:
        conn.close()
    return change_id


def a_kept_name(case, library):
    """The first photo's name kept in its file before any rename (photos.preserve_names): Smart Rename's first change,
    made alone, of a photo the rename here does not rename."""
    kept = photo_actions.preserve_names(library, [case.photo[1]], field_values.opened(EXIFTOOL))
    return kept.details.get("change") if kept.changed else None


def a_clear_duplicates(case, library):
    """The sweep of captions repeating their photo's title (scripts/clear_duplicate_captions.py --apply): the first
    photo's Description cleared, its title kept."""
    cleared = tagging.clear_duplicate_captions(library, field_values.opened(EXIFTOOL), apply=True)
    return cleared.details.get("change") if cleared.changed else None


def a_rotate(case, library):
    """Rotate left on the sixth photo, the one the later pass gives a face with a crop: the turn drops the crops and the
    record of what looked at the photo (tagpup.services.rotation), which no change holds (a crop is a cache)."""
    turned = photo_actions.rotate_photos(library, [case.photo[6]], "left", field_values.opened(EXIFTOOL))
    return turned.details.get("change") if turned.changed else None


def a_migration(case, library):
    """The synthetic additive step (migration_names.ahead, entered by the pair for as long as it runs): its record."""
    if not schema.ensure(library.path):
        return None
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return conn.execute("SELECT MAX(id) FROM changes").fetchone()[0]
    finally:
        conn.close()


def write_outside(path, values):
    """Another program writing the file."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=values, params=["-overwrite_original"])


def a_sync(case, library):
    """A caption written into the fourth photo by another program, then a sync: its row re-read."""
    write_outside(case.photo[4], {"XMP:Description": "Ferry at low tide"})
    with field_values.opened(EXIFTOOL) as fields:
        synced = sync.sync(library, apply=True, fields=fields)
    return synced.details.get("change") if synced.changed else None


def a_sync_removal(case, library):
    """The seventh photo's file deleted by another program, then a sync of the whole library: its row removed with its
    named face, the journal's (#2151; a removal the owner clicks is not journaled, DECISIONS 2026-10-30)."""
    os.remove(case.photo[7])
    with field_values.opened(EXIFTOOL) as fields:
        synced = sync.sync(library, apply=True, fields=fields)
    if not (synced.details.get("changed") or {}).get("removed"):
        return None
    return synced.details.get("change")


def a_refresh(case, library):
    """A caption written into the fifth photo by another program, then its row re-read from the file and recorded as the
    deleted refresh_rows recorded it: one change, "refresh_rows", of rows from files, each held to what it was when
    read, on the maintenance scaffold."""
    write_outside(case.photo[5], {"XMP:Description": "Ferry in the rain"})
    path = case.photo[5]
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        ids = inspection.ids_of_stored(conn, [path])
        tags, _people, captions, raw, mtime, size, _doc = store_photos.row_as_recorded(conn, path)
        found = {path: {"mtime": mtime, "size": size, "tags": tags, "captions": captions, "raw_metadata": raw}}
        with field_values.opened(EXIFTOOL) as fields:
            records, to_write, _differing, _unreadable, identities = refresh_rows.reread(conn, ids, fields)
    finally:
        conn.close()

    def plan(_library):
        return maintenance.Plan(size=len(to_write), work=refresh_rows.edits_for(records, to_write, found, identities, ids))

    refreshed = maintenance.run(library, "refresh_rows", plan, lambda planned: planned.work, apply=True,
                                kinds=("from_files",))
    return refreshed.details.get("change") if refreshed.changed else None


def a_save(case, library):
    """Save on the second photo: a caption and a tag (the photo panel)."""
    saved = tagging.save_photo(library, case.photo[2], "Ferry at the quay", ["Harbour/Ferry", "Harbour/Quay"], None,
                               field_values.opened(EXIFTOOL), "", base=page_writes.base(library, case.photo[2]))
    return saved.details.get("change") if saved.changed else None


def a_smart_rename(case, library):
    renamed = photo_actions.smart_rename(library, [case.photo[3]], "Quay", "{grouping} - {index}", field_values.opened(EXIFTOOL))
    return renamed.details.get("change") if renamed.changed else None


def a_bake(case, library):
    """A folder's bake as the deleted `bake-photos --apply` recorded it, a kind the live journals hold
    (tagpup.services.journal.RETIRED): each photo is given its photo vector first, stamped as its file is now, as the
    indexer stores it (the library as built holds none); the photos of the first photo's folder the bake plans to bake
    (baking.plan_photo) are written as one change under baking.OPERATION by the writer it used, file_changes.write_fields."""
    conn = db.connect(library.path)
    try:
        key = library_settings.photo_key_on(conn)
        rng = np.random.default_rng(11)
        for _photo_id, path in inspection.ids_and_paths(conn):
            if os.path.exists(path):
                found = rng.normal(size=bake.PHOTO_DIM).astype(np.float32)
                mtime, size = shares.read_stamp(path)
                embeddings.put(conn, path, key, mtime, size, (found / np.linalg.norm(found)).tobytes(), None)
        conn.commit()
        folder = os.path.dirname(case.photo[1])
        stored = store_roots.to_row(conn, folder)
        group = [path for _photo_id, path in inspection.ids_and_paths(conn)
                 if os.path.exists(path) and paths.key(os.path.dirname(path)) == paths.key(folder)]
    finally:
        conn.close()
    planned = {}
    for path in group:
        plan = baked_files.plan(library.path, path, "the detector of the new model")
        if isinstance(plan, baking.ToBake):
            planned[paths.key(path)] = (path, plan.values)
    if not planned:
        return None
    written = file_changes.write_fields(
        library, baking.OPERATION, field_values.opened(EXIFTOOL), [path for path, _values in planned.values()], list(bake.WRITTEN.values()),
        lambda path, _held: file_changes.Plan(after=planned[paths.key(path)][1]), summary={"folder": stored})
    return written.details.get("change")


#: The kinds made by a writer here, earlier or later: name -> make.
WRITERS = {
    "settings": a_settings_change,
    "later pass": a_later_pass,
    "migration": a_migration,
    "sync": a_sync,
    "sync removal": a_sync_removal,
    "refresh": a_refresh,
    "save photo": a_save,
    "smart rename": a_smart_rename,
    "original names": a_kept_name,
    "clear duplicates": a_clear_duplicates,
    "rotate": a_rotate,
    "bake": a_bake,
}
#: The changes the library holds when it is built: earlier kinds only (name -> the case's attribute holding the id).
BUILT = {"stamp": "stamp_change", "pass": "pass_change", "switch": "switch_change"}

#: Kinds made again for each later kind: a rename's undo knows the renamed file by what a copy does not keep.
MADE_EACH_TIME = {"smart rename"}


def sentence(template):
    """The words of a refusal's sentence that do not vary: its longest part between two of its formats."""
    return max(re.split(r"%\(?\w*\)?[sd]", template), key=len).strip()


#: Earlier kinds never undone, whatever came after -- a stamp, a migration's record, the switch to the new face model --
#: and what the refusal says.
NEVER_UNDONE = {"stamp": library_settings.NOT_UNDONE, "migration": "a migration is not undone",
                "switch": library_settings.NOT_FACENET}
#: (earlier, later) whose undo is refused, and what the refusal says: every other pair a library can hold is undone.
EXPECTED_REFUSED = {
    # A bake and a rename of one photo file, either way round: the later one first (file_journal.newer_overlapping, #1926).
    ("smart rename", "bake"): "applied after it, wrote the same photo files: undo it first",
    ("bake", "smart rename"): "applied after it, wrote the same photo files: undo it first",
    # The sync's row of the photo the bake then wrote: the row says what the file holds now (journal.refusal, #1951).
    ("sync", "bake"): sentence(journal.FILE_WRITTEN_SINCE),
    ("refresh", "bake"): sentence(journal.FILE_WRITTEN_SINCE),
    # A bake and a save of one photo, either way round: the same rule as the rename's.
    ("save photo", "bake"): "applied after it, wrote the same photo files: undo it first",
    ("bake", "save photo"): "applied after it, wrote the same photo files: undo it first",
    # The kept name and the sweep write the first photo, as the bake does: each of the three pairs either way round.
    ("original names", "bake"): "applied after it, wrote the same photo files: undo it first",
    ("bake", "original names"): "applied after it, wrote the same photo files: undo it first",
    ("clear duplicates", "bake"): "applied after it, wrote the same photo files: undo it first",
    ("bake", "clear duplicates"): "applied after it, wrote the same photo files: undo it first",
    ("original names", "clear duplicates"): "applied after it, wrote the same photo files: undo it first",
    ("clear duplicates", "original names"): "applied after it, wrote the same photo files: undo it first",
}
#: (earlier, later) whose refusal undoing the later change first does not lift: the later write's undo stamps the sync's
#: row again (History refusing it too, with the same sentence; #1951, decided).
NOT_LIFTED_IN_ORDER = {("sync", "bake"), ("refresh", "bake")}


def entered(*kinds):
    """The context a pair runs in: the synthetic step known while a migration is one of its kinds."""
    return migration_names.ahead() if "migration" in kinds else contextlib.nullcontext()


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class UndoAfterEveryLaterKind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls, prefix="undo_matrix_")
        cls.folder = paths.stored(os.path.join(cls.home.root, "Quay"))
        cls.kept = os.path.join(cls.home.root, "kept")
        cls.base = Library(cls.home.library("harbour.db"))
        cls.build(cls.base)
        cls.keep_files("base")

    @classmethod
    def build(cls, library):
        """harbour: seven photos (real JPEGs, their keywords written
        by ExifTool; the first one's caption repeating its title), faces as the pass left them -- matched faces holding FaceNet's vector and the new model's, faces
        the old detector alone found holding FaceNet's, one alone in the sixth photo -- named automatically, by hand,
        nobody, excluded or undecided; the pass one change; switched to the new model after it. Stamped as the live
        libraries were before the face model was a setting: no faces.model row, which the switch inserts."""
        from PIL import Image

        before_the_face_model(library.path)
        library_actions.create(library.path)
        os.makedirs(cls.folder)
        library_actions.record_added(library, [cls.folder])
        cls.photo = {n: os.path.join(cls.folder, "ferry %d.jpg" % n) for n in range(1, 8)}
        fields = {n: {"XMP:Subject": ["Tamsin Okoro" if n == 1 else "Harbour/Ferry"]} for n in cls.photo}
        # The first photo's caption repeats its title, as a save wrote both before 2026-11-06: the sweep's to clear.
        fields[1].update({"XMP:Title": "Ferry", "XMP:Description": "Ferry"})
        with ExifToolSession(executable=EXIFTOOL) as et:
            for n, path in cls.photo.items():
                Image.new("RGB", (64, 48), (90, 110, 20 * n)).save(path, "JPEG")
                et.set_tags([path], tags=fields[n], params=["-overwrite_original"])
        rng = np.random.default_rng(7)
        conn = db.connect(library.path)
        try:
            cls.stamp_change = conn.execute("SELECT MIN(id) FROM changes").fetchone()[0]
            tamsin, odile = person_of(conn, "Tamsin Okoro"), person_of(conn, "Odile Farrant")
            for n, path in cls.photo.items():
                photo_rows.add_read(conn, path, fields[n])
            matched_in = {n: [] for n in cls.photo}

            def face(n, matched, automatic=None, by_hand=None, nobody=False, excluded=None):
                face_id = store_faces.insert(conn, cls.photo[n], [n, n, 40 + n, 40 + n], vector(rng), model=FACENET,
                                             name=automatic, crop=bytes([n]) * 64, prob=0.98)
                if matched:
                    matched_in[n].append((face_id, vector(rng)))
                if by_hand is not None:
                    store_faces.name(conn, [face_id], by_hand)
                if nobody:
                    store_faces.unname(conn, [face_id], source="manual")
                if excluded is not None:
                    store_faces.exclude(conn, [face_id], excluded)

            face(1, False)
            face(1, True, automatic=odile)
            face(2, False, by_hand=tamsin)
            face(2, False, excluded="not a face")
            face(2, True, nobody=True)
            face(3, True, by_hand=odile)
            face(3, True, excluded="a statue")
            face(4, True)
            face(5, False, automatic=tamsin)
            face(5, True)
            face(6, False)
            face(7, False, by_hand=odile)
            cls.pass_change = journal.record(conn, journal_service.FACE_PASS % NEW, [],
                                             {"folder": store_roots.to_row(conn, cls.folder), "matched": 0, "new": 0})
            for n, matched in matched_in.items():
                if matched:
                    face_rows.as_a_face_pass_gave(conn, cls.pass_change, cls.photo[n], NEW, matched, [],
                                                  {"matched": len(matched), "new": 0})
            conn.commit()
        finally:
            conn.close()
        switched = library_settings.change(library, {"faces.model": NEW}, acknowledged=["face-model"])
        assert switched.changed == 1, switched.refused
        cls.switch_change = switched.details["change"]

    # ---- copies ------------------------------------------------------------------------------------------------------

    @classmethod
    def keep_files(cls, name):
        shutil.copytree(cls.folder, os.path.join(cls.kept, name))

    @classmethod
    def put_files_back(cls, name):
        shutil.rmtree(cls.folder)
        shutil.copytree(os.path.join(cls.kept, name), cls.folder)

    def copy(self, source, name):
        target = Library(self.home.library(name + ".db"))
        db.copy_database(source.path, target.path)
        return target

    # ---- what a refusal must leave alone -----------------------------------------------------------------------------

    def digest(self, library):
        """Every table's row count, the generations, every change's status, and every photo file's name, size and
        modified time."""
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            tables = [name for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name") if not name.startswith("sqlite_")]
            counts = {table: conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0] for table in tables}
            found = {"rows": counts, "generations": generations.values(conn),
                     "changes": conn.execute("SELECT id, status FROM changes ORDER BY id").fetchall()}
        finally:
            conn.close()
        found["files"] = sorted((entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                                for entry in os.scandir(self.folder))
        return found

    def changes_after(self, library, change_id):
        """The ids of the changes after `change_id` not undone, newest first."""
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return [found for (found,) in conn.execute(
                "SELECT id FROM changes WHERE id > ? AND status != 'undone' ORDER BY id DESC", (change_id,))]
        finally:
            conn.close()

    # ---- the scenario ------------------------------------------------------------------------------------------------

    def undo_from_history(self, library, change_id):
        """History listed, Undo rehearsed, Undo applied: (why the listing gives no button, the rehearsal, the undo)."""
        listed = journal_service.refusals(library, [{"id": change_id}])[change_id]
        before = self.digest(library)
        rehearsed = journal_service.undo(library, change_id, apply=False, fields=field_values.opened(EXIFTOOL))
        self.assertEqual(before, self.digest(library), "the rehearsal wrote")
        undone = journal_service.undo(library, change_id, apply=True, fields=field_values.opened(EXIFTOOL))
        if undone.refused:
            self.assertEqual(before, self.digest(library), "a refused undo wrote")
        return listed, rehearsed, undone

    def test_an_earlier_change_is_undone_after_every_later_kind_as_history_offers_it(self):
        outcomes, n, ordered = {}, 0, 0
        earlier = list(BUILT) + list(WRITERS)
        later = list(WRITERS)
        self.assertEqual(sorted(earlier), sorted(row for rows in OPERATIONS.values() for row in rows),
                         "a row of the matrix is not of exactly one kind (tests/undo_kinds.py)")
        for a in earlier:
            with entered(a):
                n += 1
                self.put_files_back("base")
                after_a = self.copy(self.base, "a%d" % n)
                change_a = getattr(self, BUILT[a]) if a in BUILT else WRITERS[a](self, after_a)
                if change_a is not None:
                    operation = journal.history(after_a.path, change_id=change_a)[0]["operation"]
                    self.assertIn(a, OPERATIONS.get(journal_service.kind_of(operation), ()),
                                  "the row is not of the kind tests/undo_kinds.py says")
                if a in MADE_EACH_TIME:
                    after_a = None
                self.assertIsNotNone(change_a, "%s was not made on the library as built" % a)
                self.keep_files("a%d" % n)
                for b in later:
                    if b == a:
                        continue
                    with self.subTest(earlier=a, later=b), contextlib.ExitStack() as context:
                        # The earlier kind is made before the later kind's context is entered: a smart rename's service
                        # migrates the copy, and a migration made inside that context would find nothing to do.
                        if after_a is None:
                            self.put_files_back("base")
                            library = self.copy(self.base, "a%d_%s" % (n, b.replace(" ", "_")))
                            change_a = WRITERS[a](self, library)
                        else:
                            self.put_files_back("a%d" % n)
                            library = self.copy(after_a, "a%d_%s" % (n, b.replace(" ", "_")))
                        if a != "migration":
                            context.enter_context(entered(b))
                        made = WRITERS[b](self, library)
                        if made is None:
                            outcomes[(a, b)] = None
                            continue
                        listed, rehearsed, undone = self.undo_from_history(library, change_a)
                        outcomes[(a, b)] = bool(undone.refused)
                        self.assertEqual((listed is None, listed is None),
                                         (not rehearsed.refused, not undone.refused),
                                         "History, the rehearsal and the undo disagree: %r, %r, %r" % (
                                             listed, rehearsed.refused, undone.refused))
                        said = NEVER_UNDONE.get(a) or EXPECTED_REFUSED.get((a, b))
                        self.assertEqual(bool(said), bool(undone.refused), undone.refused)
                        if said:
                            self.assertIn(said, " ".join(" ".join(undone.refused).split()) if isinstance(
                                undone.refused, list) else " ".join(undone.refused.split()))
                        if said and a not in NEVER_UNDONE and made is not True and b not in NEVER_UNDONE:
                            # Undone in order: the later changes first (a rename is two), newest first, then the earlier.
                            for later_id in self.changes_after(library, change_a):
                                self.assertFalse(journal_service.undo(library, later_id, apply=True,
                                                                      fields=field_values.opened(EXIFTOOL)).refused)
                            listed, rehearsed, undone = self.undo_from_history(library, change_a)
                            if (a, b) in NOT_LIFTED_IN_ORDER:
                                self.assertTrue(undone.refused)
                                self.assertEqual((listed, listed), (rehearsed.refused, undone.refused))
                                continue
                            self.assertEqual((None, False), (listed, bool(undone.refused)), undone.refused)
                            ordered += 1
                        if not undone.refused:
                            self.assertTrue(rehearsed.details["rehearsal"]["exact"], rehearsed.details["rehearsal"])
                            self.assertEqual("undone", journal.history(library.path, change_id=change_a)[0]["status"])
        made = {pair for pair, refused in outcomes.items() if refused is not None}
        self.assertTrue(set(EXPECTED_REFUSED) <= made, "a pair the promise names was not made")
        self.assertEqual(set(), {pair for pair, refused in outcomes.items() if refused is None},
                         "a later kind's writer made nothing after an earlier kind")
        # Every later kind after every earlier one, the diagonal not generated: 15 earlier by 12 later, less 12 (the
        # retirings' kinds and Suggest's step left with retire-faces and retire-vectors, 2026-10-25; a rotation came in,
        # 2026-10-27; the switch back went, 2026-10-28; a sync's removal came in, 2026-10-30, #2160; a kept name and the
        # sweep of repeated captions came in, #2748).
        self.assertEqual(len(earlier) * len(later) - len(later), len(made), "the pairs a library can hold were not made")
        self.assertEqual(168, len(made))
        self.assertEqual(10, ordered, "the pairs undone in order: a bake and the rename, the save, the kept name or the "
                                      "sweep after it, each of them and the bake after it, the kept name and the sweep "
                                      "of one photo either way round")
        # Measured about 90 s alone with the sync's removal, before the kept name and the sweep came in; the suite's
        # tier timing reports it, so no wall-clock assertion.

    def test_a_sync_is_not_offered_once_a_save_wrote_its_photo(self):
        """Another program writes a caption into a photo and a sync re-reads its row; then the owner saves the photo
        (Save writes its file, and the row says what the file holds). History offers no Undo for the sync, saying
        why, and the rehearsal and the undo refuse with the same sentence, writing nothing; undoing the Save first
        stamps the row again, so the sync still is not offered (#1951)."""
        self.put_files_back("base")
        library = self.copy(self.base, "sync_then_save")
        synced = a_sync(self, library)
        saved = tagging.save_photo(library, self.photo[4], "Ferry at the quay", ["Harbour/Ferry"], None, field_values.opened(EXIFTOOL), "",
                                   base=page_writes.base(library, self.photo[4]))
        self.assertTrue(saved.changed, saved.refused)
        said = journal.FILE_WRITTEN_SINCE % {
            "other": saved.details["change"], "operation": "save photo", "photos": 1, "change": synced}
        for undone_first in (False, True):
            with self.subTest(save_undone_first=undone_first):
                if undone_first:
                    self.assertFalse(journal_service.undo(library, saved.details["change"], apply=True,
                                                          fields=field_values.opened(EXIFTOOL)).refused)
                entry = journal_service.history(library, change_id=synced)["changes"][0]
                self.assertEqual(said, entry["why_not"], "History offers the sync's Undo")
                listed, rehearsed, undone = self.undo_from_history(library, synced)
                self.assertEqual((said, said), (rehearsed.refused, undone.refused))

    def test_a_save_is_undone_once_the_index_bake_writing_its_photo_is_finished(self):
        """The owner saves a photo; the index's bake (baking.INDEX_OPERATION, internal) has the photo's file `writing`
        after it -- another process under way, or one killed with the file written and not yet recorded. History lists
        no bake; the save's Undo is refused "being written now" while the file is `writing` (one writer of a file at a
        time), and once the bake is finished (settled at the next start) it is undone past it."""
        self.put_files_back("base")
        library = self.copy(self.base, "save_then_crashed_bake")
        saved = a_save(self, library)
        self.assertIsNotNone(saved)
        found = bake.WRITTEN[bake.FACES_FOUND]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                file_changes.write_fields(library, baking.INDEX_OPERATION, field_values.opened(EXIFTOOL), [self.photo[2]], [found],
                                          lambda _path, _held: file_changes.Plan(after={found: ["0"]}))
        self.assertEqual([baking.INDEX_OPERATION], [each.operation for each in file_journal.unfinished(library.path)])
        self.assertEqual([], [entry for entry in journal_service.history(library, limit=1000)["changes"]
                              if entry["operation"] == baking.INDEX_OPERATION])
        said = journal.FILE_BEING_WRITTEN % {"change": saved}
        listed, rehearsed, undone = self.undo_from_history(library, saved)
        self.assertIn(said, listed or "")
        self.assertIn(said, rehearsed.refused or "")
        self.assertIn(said, undone.refused or "")
        file_changes.settle(library, field_values.opened(EXIFTOOL))
        self.assertEqual([], file_journal.unfinished(library.path))
        listed, rehearsed, undone = self.undo_from_history(library, saved)
        self.assertEqual((None, None, None), (listed, rehearsed.refused, undone.refused))
        self.assertEqual("undone", journal.history(library.path, change_id=saved)[0]["status"])

    def test_a_sync_is_offered_again_once_the_rename_after_it_is_undone(self):
        """Smart Rename names a photo (its first name kept in the file); the owner moves it into a folder inside, in
        Explorer, and a sync follows it, recording the row's new path; Smart Rename names it again, a rename alone (the
        first name is kept already). History offers no Undo for the sync while that rename stands, saying so, as the
        rehearsal and the undo do; once the rename is undone the sync is offered, and undone."""
        self.put_files_back("base")
        library = self.copy(self.base, "sync_then_rename")
        before = set(os.listdir(self.folder))
        self.assertTrue(photo_actions.smart_rename(library, [self.photo[4]], "Quay", "{grouping} - {index}",
                                                   field_values.opened(EXIFTOOL)).changed)
        (named,) = set(os.listdir(self.folder)) - before
        os.makedirs(os.path.join(self.folder, "Pier"))
        moved = os.path.join(self.folder, "Pier", named)
        os.rename(os.path.join(self.folder, named), moved)
        with field_values.opened(EXIFTOOL) as fields:
            synced = sync.sync(library, apply=True, fields=fields).details.get("change")
        self.assertIsNotNone(synced, "the sync followed no move")
        renamed = photo_actions.smart_rename(library, [moved], "Pier", "{grouping} - {index}", field_values.opened(EXIFTOOL))
        self.assertTrue(renamed.changed, renamed.refused)
        (rename,) = self.changes_after(library, synced)   # the rename alone: the first name is in the file
        said = journal.FILE_RENAMED_SINCE % {"other": rename, "operation": "smart rename", "photos": 1, "change": synced}
        listed, rehearsed, undone = self.undo_from_history(library, synced)
        self.assertEqual((said, said, said), (listed, rehearsed.refused, undone.refused))
        self.assertFalse(journal_service.undo(library, rename, apply=True, fields=field_values.opened(EXIFTOOL)).refused)
        listed, rehearsed, undone = self.undo_from_history(library, synced)
        self.assertEqual((None, None), (listed, undone.refused))
        self.assertEqual("undone", journal.history(library.path, change_id=synced)[0]["status"])


if __name__ == "__main__":
    unittest.main()
