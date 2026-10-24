"""Undo, from History, a change after a later change of another kind (scenario D2; #1805). Someone makes a change, then
a later one of another kind, then opens History and clicks Undo on the first. For every ordered pair of the kinds a
library really holds in sequence, each made by its real writer, the History dialog, the rehearsal and the undo agree
(tagpup.services.journal.why_not is their one account): the listing offers the Undo button exactly when the rehearsal and
the undo do not refuse; a refusal writes nothing -- no row, no generation, no change's status, no file; an undo allowed
is a round trip the rehearsal found exact; and whether it is allowed is what the owner was promised (EXPECTED_REFUSED):
a stamp, a migration and a vectors' retiring are never undone; a face pass not while the retiring after it stands; a
retiring not while a face was made since in a photo it emptied, nor once its vectors' model was retired; a switch of
face model not where the switch back would be refused.

The kinds: a stamp of the settings, a change of the settings, a switch of face model and a switch back, a face pass, a
retiring of the old detector's faces and of the old model's vectors, a migration's record (a synthetic additive step,
tests/migration_names.py), a sync and a refresh of a row re-read from its file, Save on a photo and Smart Rename (real
ExifTool, a change of photo files), and Suggest's detection (no change of its own, a later step only). Left to the
guard (tests/test_every_change_kind_has_an_undo_check.py): dedupe_faces and merge_duplicate_person_tags (no live library
holds a duplicate face or a bare person tag, counted 2026-10-24), the bulk edits, tag renames and face namings (each a
change of photo files or of faces whose way back is the journal's alone, None in UNDO_CHECKS, as Save's is).

Lifted (each scenario a pair here): test_retiring_the_old_detectors_faces' pass under a standing retiring, retiring
after a pass, and retiring after Suggest's detection; test_retiring_the_old_models_vectors' undo of the retiring and the
switch once the vectors are retired; test_history_routes' listing agreeing with the rehearsal; test_journal's migration
never undone.

One library is built (harbour: six photos read by the index, faces as the old detector and the new model's pass left
them, switched to the new model), copied after each earlier change, and the copy taken again for each later one, with
the photo files put back as the earlier change left them. Fictional names; a home of the test's own; rows as the
indexer and the pass store them.
"""
import contextlib
import os
import shutil
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import migration_names  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import FACENET, person_of  # noqa: E402

import numpy as np  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import face_pass, refresh_rows, retire_faces, sync, tagging  # noqa: E402
from tagpup.services import faces as face_records  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, generations, journal, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
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


def a_switch_back(case, library):
    changed = library_settings.change(library, {"faces.model": FACENET}, acknowledged=["face-model"])
    return changed.details.get("change") if changed.changed else None


def a_later_pass(case, library):
    """A pass under the new model that finds a face in the sixth photo: its own change, by the pass's writer."""
    path = case.photo[6]
    conn = db.connect(library.path)
    try:
        change_id = journal.record(conn, face_pass.OPERATION % NEW, [],
                                   {"folder": store_roots.to_row(conn, os.path.dirname(path)), "matched": 0, "new": 0})
        store_faces.give_vectors(conn, change_id, path, NEW, [],
                                 [([7, 7, 27, 27], 0.99, vector(np.random.default_rng(8)), bytes([7]) * 64)],
                                 {"matched": 0, "new": 1})
        conn.commit()
    finally:
        conn.close()
    return change_id


def a_retiring(case, library):
    planned = retire_faces.plan(library)
    if planned.refused or not planned.photo_ids:
        return None
    retired = retire_faces.apply(library, planned, clear_manual_names=True)
    return retired.changes[0] if retired.changes else None


def a_vectors_retiring(case, library):
    """retire-vectors, after retire-faces as the owner runs them when faces hold the old model's vector alone."""
    planned = retire_faces.plan_vectors(library)
    if planned.refused and a_retiring(case, library) is not None:
        planned = retire_faces.plan_vectors(library)
    if planned.refused or not planned.vectors:
        return None
    return retire_faces.apply_vectors(library, planned).change_id


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
    synced = sync.sync(library, apply=True, exiftool_path=EXIFTOOL)
    return synced.details.get("change") if synced.changed else None


def a_refresh(case, library):
    """A caption written into the fifth photo by another program, then refresh_rows."""
    write_outside(case.photo[5], {"XMP:Description": "Ferry in the rain"})
    refreshed = refresh_rows.refresh_rows(library, EXIFTOOL, apply=True)
    return refreshed.details.get("change") if refreshed.changed else None


def a_save(case, library):
    """Save on the second photo: a caption and a tag (the photo panel)."""
    saved = tagging.save_photo(library, case.photo[2], "Ferry at the quay", ["Harbour/Ferry", "Harbour/Quay"], None,
                               EXIFTOOL, "")
    return saved.details.get("change") if saved.changed else None


def a_smart_rename(case, library):
    renamed = photo_actions.smart_rename(library, [case.photo[3]], "Quay", "{grouping} - {index}", EXIFTOOL)
    return renamed.details.get("change") if renamed.changed else None


def suggest_detects(case, library):
    """Suggest's detection in the sixth photo, which it looks at again only when it holds no face: no change."""
    embedding = np.random.default_rng(9).standard_normal(512).astype(np.float32).tolist()
    return True if face_records.record_detected(
        library.path, case.photo[6], [{"box": [7, 7, 27, 27], "prob": 0.99, "embedding": embedding}], model=NEW) else None


#: The kinds made by a writer here, earlier or later: name -> make.
WRITERS = {
    "settings": a_settings_change,
    "switch back": a_switch_back,
    "later pass": a_later_pass,
    "retire faces": a_retiring,
    "retire vectors": a_vectors_retiring,
    "migration": a_migration,
    "sync": a_sync,
    "refresh": a_refresh,
    "save photo": a_save,
    "smart rename": a_smart_rename,
}
#: The changes the library holds when it is built: earlier kinds only (name -> the case's attribute holding the id).
BUILT = {"stamp": "stamp_change", "pass": "pass_change", "switch": "switch_change"}
#: Later steps that make no change of their own.
STEPS = {"suggest detects": suggest_detects}
#: Kinds made again for each later kind: a rename's undo knows the renamed file by what a copy does not keep.
MADE_EACH_TIME = {"smart rename"}

#: Earlier kinds never undone, whatever came after: a stamp, a migration's record, a vectors' retiring.
NEVER_UNDONE = {"stamp", "migration", "retire vectors"}
#: (earlier, later) whose undo is refused, and by what: every other pair a library can hold is undone.
EXPECTED_REFUSED = {
    # A face pass, while the retiring after it stands (retire_faces.pass_check).
    ("pass", "retire faces"), ("pass", "retire vectors"),
    ("later pass", "retire faces"), ("later pass", "retire vectors"),
    # The switch, once a switch back changed its row (the journal), or no face holds FaceNet's vector (NONE_HELD).
    ("switch", "switch back"), ("switch", "retire vectors"),
    # The retiring, once a face was made since in a photo it emptied (RETIRING_UNDER), or its vectors were retired.
    ("retire faces", "later pass"), ("retire faces", "suggest detects"), ("retire faces", "retire vectors"),
}


def entered(*kinds):
    """The context a pair runs in: the synthetic step known while a migration is one of its kinds."""
    return migration_names.ahead() if "migration" in kinds else contextlib.nullcontext()


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class UndoAfterEveryLaterKind(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls, prefix="undo_matrix_")
        patched = mock.patch.object(library_settings, "UNPASSED_SHARE_ALLOWED", 0.1)
        patched.start()
        cls.addClassCleanup(patched.stop)
        cls.folder = paths.stored(os.path.join(cls.home.root, "Quay"))
        cls.kept = os.path.join(cls.home.root, "kept")
        cls.base = Library(cls.home.library("harbour.db"))
        cls.build(cls.base)
        cls.keep_files("base")

    @classmethod
    def build(cls, library):
        """harbour, as test_retiring_the_old_detectors_faces' library: six photos (real JPEGs, their keywords written
        by ExifTool), faces as the pass left them -- matched faces holding FaceNet's vector and the new model's, faces
        the old detector alone found holding FaceNet's, one alone in the sixth photo -- named automatically, by hand,
        nobody, excluded or undecided; the pass one change; switched to the new model after it."""
        from PIL import Image

        library_actions.create(library.path)
        os.makedirs(cls.folder)
        library_actions.record_added(library, [cls.folder])
        cls.photo = {n: os.path.join(cls.folder, "ferry %d.jpg" % n) for n in range(1, 7)}
        fields = {n: {"XMP:Subject": ["Tamsin Okoro" if n == 1 else "Harbour/Ferry"]} for n in cls.photo}
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
                face_id = store_faces.insert(conn, cls.photo[n], [n, n, 20 + n, 20 + n], vector(rng), model=FACENET,
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
            cls.pass_change = journal.record(conn, face_pass.OPERATION % NEW, [],
                                             {"folder": store_roots.to_row(conn, cls.folder), "matched": 0, "new": 0})
            for n, matched in matched_in.items():
                if matched:
                    store_faces.give_vectors(conn, cls.pass_change, cls.photo[n], NEW, matched, [],
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

    # ---- the scenario ------------------------------------------------------------------------------------------------

    def undo_from_history(self, library, change_id):
        """History listed, Undo rehearsed, Undo applied: (why the listing gives no button, the rehearsal, the undo)."""
        listed = journal_service.refusals(library, [{"id": change_id}])[change_id]
        before = self.digest(library)
        rehearsed = journal_service.undo(library, change_id, apply=False, exiftool_path=EXIFTOOL)
        self.assertEqual(before, self.digest(library), "the rehearsal wrote")
        undone = journal_service.undo(library, change_id, apply=True, exiftool_path=EXIFTOOL)
        if undone.refused:
            self.assertEqual(before, self.digest(library), "a refused undo wrote")
        return listed, rehearsed, undone

    def test_an_earlier_change_is_undone_after_every_later_kind_as_history_offers_it(self):
        started, outcomes, n = time.monotonic(), {}, 0
        earlier = list(BUILT) + list(WRITERS)
        later = list(WRITERS) + list(STEPS)
        for a in earlier:
            with entered(a):
                n += 1
                self.put_files_back("base")
                after_a = self.copy(self.base, "a%d" % n)
                change_a = getattr(self, BUILT[a]) if a in BUILT else WRITERS[a](self, after_a)
                if a in MADE_EACH_TIME:
                    after_a = None
                self.assertIsNotNone(change_a, "%s was not made on the library as built" % a)
                self.keep_files("a%d" % n)
                for b in later:
                    if b == a:
                        continue
                    with self.subTest(earlier=a, later=b), entered(b) if a != "migration" else contextlib.nullcontext():
                        if after_a is None:
                            self.put_files_back("base")
                            library = self.copy(self.base, "a%d_%s" % (n, b.replace(" ", "_")))
                            change_a = WRITERS[a](self, library)
                        else:
                            self.put_files_back("a%d" % n)
                            library = self.copy(after_a, "a%d_%s" % (n, b.replace(" ", "_")))
                        made = (WRITERS.get(b) or STEPS[b])(self, library)
                        if made is None:
                            outcomes[(a, b)] = None
                            continue
                        listed, rehearsed, undone = self.undo_from_history(library, change_a)
                        outcomes[(a, b)] = bool(undone.refused)
                        self.assertEqual((listed is None, listed is None), (not rehearsed.refused, not undone.refused),
                                         "History, the rehearsal and the undo disagree: %r, %r, %r" % (
                                             listed, rehearsed.refused, undone.refused))
                        self.assertEqual(a in NEVER_UNDONE or (a, b) in EXPECTED_REFUSED, bool(undone.refused),
                                         undone.refused)
                        if not undone.refused:
                            self.assertTrue(rehearsed.details["rehearsal"]["exact"], rehearsed.details["rehearsal"])
                            self.assertEqual("undone", journal.history(library.path, change_id=change_a)[0]["status"])
        made = {pair for pair, refused in outcomes.items() if refused is not None}
        self.assertTrue(EXPECTED_REFUSED <= set(outcomes) and EXPECTED_REFUSED <= made, "a pair the promise names was not made")
        self.assertGreater(len(made), 100, "the pairs a library can hold were not made")
        self.assertLess(time.monotonic() - started, 120, "the matrix is to stay under two minutes")


if __name__ == "__main__":
    unittest.main()
