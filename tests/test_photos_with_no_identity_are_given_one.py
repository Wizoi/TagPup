"""The one-off that gives an identity to the photos holding none (scripts/mint_identities.py; plan
docs/plans/own-metadata-library.md, B1; scenario F3), as the owner runs it on a library whose photos were baked before
the bake minted one (56,272 such in photo_index, 2026-11-27): each holds its bake and no DocumentID, its row none.

What the owner lives through:
- a dry run counts per folder the photos it would mint, one that does not decode and one that may be an incomplete copy
  (#407), and writes nothing, a file or a row; its parts account for every photo of the folder;
- --apply mints each photo once, through the index's bake (its id alone: the bake is not rewritten), its row holding
  what its file holds; the one that does not decode and the incomplete copy are counted and their bytes untouched;
- a second run mints nothing and writes no file;
- a Ctrl+C after the first of two folders stops before the second; the run after mints only the second;
- --apply is refused while TagPup runs, and while another run holds the library, writing nothing; a run leaves no
  lock file behind;
- a photo whose file holds an identity its row never recorded (another program's, about a quarter of photo_index's
  photos with none recorded) is counted by the dry run, and --apply records it in the row, its file written nothing;
  the run after finds none left;
- a Ctrl+C in a dry run says nothing was written.
"""
import json
import os
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
import baked_files  # noqa: E402
import corpus  # noqa: E402
import mint_identities  # noqa: E402
import own_home  # noqa: E402
from stand_in_models import Hold, RefusableFaces  # noqa: E402
from test_file_journal import field_of  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup import runtime as runtimes  # noqa: E402
from tagpup.core import byte_lock, paths  # noqa: E402
from tagpup.files import bake, field_values, identity, images  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import bake_after_index, baking  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
SIZE = (640, 480)
FACE = [300, 200, 400, 330]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class PhotosWithNoIdentityAreGivenOne(unittest.TestCase):

    def setUp(self):
        self.home = own_home.for_test(self, prefix="photos_with_no_identity_")
        self.db_path = self.home.library("harbour.db")
        self.assertEqual(1, library_actions.create(self.db_path).changed)
        servers = os.path.join(self.home.root, "servers")
        os.makedirs(servers)
        self.detector = RefusableFaces({"a.jpg": [(FACE, 0.99, 1)]}, vector=baked_files.face_vector)
        vector = baked_files.unit(1152, 99).tolist()
        for patcher in (mock.patch.dict(os.environ, {"TAGPUP_SERVERS": servers}),
                        mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input", return_value="input"),
                        mock.patch.object(clip.ClipModel, "embed_input", return_value=vector),
                        mock.patch.object(Runtime, "gpu_turn", lambda runtime, what, *args, **kwargs: Hold()),
                        mock.patch.object(runtimes, "on_the_card", return_value=True),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def baked_before_the_mint(self, folder, *names, held=None):
        """`names` in `folder`, indexed and baked as the bake before stage M left them: no identity, in the file or the
        row; but for those of `held` ({name: DocumentID}), whose file another program gave one before the index read it,
        and whose row, as an index that did not ask for it left it, records none."""
        folder = paths.stored(os.path.join(self.home.root, folder))
        os.makedirs(folder, exist_ok=True)
        held = held or {}
        made = [corpus.jpeg(os.path.join(folder, name), {"XMP-dc:Title": "Pier at noon",
                                                         **({identity.DOCUMENT_ID_FIELD: held[name]} if name in held else {})},
                            EXIFTOOL, size=SIZE, colour=(40 + 30 * n, 160, 200 - 30 * n)) for n, name in enumerate(names)]
        with mock.patch.object(bake_after_index, "_minted", return_value=None):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder])
        self.assertEqual(0, result.exit_code, result.output)
        for path in made:
            self.assertEqual(held.get(os.path.basename(path)), field_of(path, identity.DOCUMENT_ID_FIELD))
            if os.path.basename(path) in held:
                self.unlearned(path)
            self.assertIsInstance(baked_files.plan(self.db_path, path, face_service.detector_of(self.detector)),
                                  baking.Current, "not baked")
        return made

    def unlearned(self, path):
        """The row of `path` as an index that never asked for its DocumentID left it (raw_metadata holding no
        DocumentID key, document_id NULL; 56,272 such rows in photo_index), at the stamp its file is at."""
        def forget(conn):
            where, params = store_roots.sql_equals(conn, "path", path)
            (raw,) = conn.execute("SELECT raw_metadata FROM photos WHERE " + where, params).fetchone()
            raw = {key: value for key, value in json.loads(raw).items() if "DocumentID" not in key}
            conn.execute("UPDATE photos SET document_id = NULL, raw_metadata = ? WHERE " + where,
                         [json.dumps(raw)] + list(params))
        db.write_with_connection(self.db_path, forget, label="a row that never learned its identity")

    def stamp_of_row(self, path):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            where, params = store_roots.sql_equals(conn, "path", path)
            return conn.execute("SELECT mtime, size FROM photos WHERE " + where, params).fetchone()
        finally:
            conn.close()

    def accounted(self):
        """A dry run, each folder's counts asserted to account for every photo of the folder (its header count): the
        run's (code, what it said)."""
        owed = {paths.key(folder): photos for folder, photos, _no_id in bake_after_index.folders_owed_an_identity(
            mint_identities.Library(self.db_path))}
        real, seen = bake_after_index.bake_folders, []

        def counted(library, fields, detector, folders, *args, **kwargs):
            baked = real(library, fields, detector, folders, *args, **kwargs)
            parts = (baked.to_mint + (baked.planned - baked.to_mint) + baked.to_record + baked.current
                     + baked.incomplete + baked.not_answered + sum(baked.cannot.values()))
            seen.append((owed[paths.key(folders[0])], parts))
            return baked
        with mock.patch.object(bake_after_index, "bake_folders", side_effect=counted):
            said = self.run_script(apply=False)
        self.assertTrue(seen)
        self.assertEqual([photos for photos, _parts in seen], [parts for _photos, parts in seen],
                         "a dry run's line does not account for every photo of its folder")
        return said

    def identities(self, *made):
        """{path: (the DocumentID its file holds, the one its row holds)}."""
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            rows = {paths.key(path): document_id for path, document_id in conn.execute("SELECT path, document_id FROM photos")}
        finally:
            conn.close()
        return {path: (field_of(path, identity.DOCUMENT_ID_FIELD), rows[paths.key(path)]) for path in made}

    def run_script(self, apply):
        said = []
        code = mint_identities.run(self.db_path, apply, out=said.append)
        return code, " ".join(" ".join(said).split())

    def bytes_of(self, *made):
        result = {}
        for path in made:
            with open(path, "rb") as handle:
                result[path] = handle.read()
        return result

    def bake_fields(self, path):
        with field_values.opened(EXIFTOOL) as et:
            return et.read([path], list(bake.WRITTEN.values()))[paths.key(path)]

    def test_each_photo_is_minted_once_and_the_one_that_does_not_decode_and_the_incomplete_copy_are_counted_untouched(self):
        a, b, broken, cut = self.baked_before_the_mint("Harbour", "a.jpg", "b.jpg", "broken.jpg", "cut.jpg")
        with open(broken, "rb") as handle:
            data = handle.read()
        scan = data.rindex(b"\xff\xda")
        with open(broken, "wb") as handle:   # its picture cut off part way: it does not decode in full
            handle.write(data[:scan + (len(data) - scan) // 2])
        with self.assertRaises(OSError):
            images.opened(broken, upright=True)
        with open(cut, "ab") as handle:      # an interrupted copy into a file given its size (#407)
            handle.write(bytes(images.ZERO_TAIL))
        before = self.bytes_of(a, b, broken, cut)

        code, said = self.accounted()
        self.assertEqual(0, code, said)
        self.assertIn("4 photo(s) in 1 folder(s) whose row records no identity", said)
        self.assertIn("all: 2 to mint (2 alone, 0 with their bake), 0 owed their bake alone, 0 already hold one in the "
                      "file, their row to be recorded, 0 current; left as they are: 1 possibly incomplete copies, 1 %s."
                      % baking.UNREADABLE, said)
        self.assertEqual(before, self.bytes_of(a, b, broken, cut), "a dry run wrote a file")
        self.assertEqual({path: (None, None) for path in (a, b)}, self.identities(a, b), "a dry run wrote a row")

        bake_before = {path: self.bake_fields(path) for path in (a, b)}
        code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertIn("all: wrote 2 identit(ies), recorded 0 from their files.", said)
        self.assertIn(bake_after_index._SAID_ALONE % 2, said)
        given = self.identities(a, b)
        for path, (held, row) in given.items():
            self.assertTrue(str(held).startswith("xmp.did:"), "no identity minted")
            self.assertEqual(held, row, "the row does not hold the identity its file holds")
            self.assertEqual(bake_before[path], self.bake_fields(path), "a current bake was rewritten")
        self.assertNotEqual(given[a], given[b])
        self.assertEqual({path: before[path] for path in (broken, cut)}, self.bytes_of(broken, cut),
                         "a photo that does not decode, or may be an incomplete copy, was written")

        written = self.bytes_of(a, b)
        code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertIn("2 photo(s) in 1 folder(s) whose row records no identity", said)
        self.assertIn("all: wrote 0 identit(ies), recorded 0 from their files.", said)
        self.assertEqual(written, self.bytes_of(a, b), "a second run wrote a photo again")
        self.assertEqual(given, self.identities(a, b))

    def test_a_ctrl_c_stops_after_the_folder_under_way_and_the_next_run_mints_only_the_rest(self):
        (a,) = self.baked_before_the_mint("Harbour", "a.jpg")
        (q,) = self.baked_before_the_mint("Quay", "q.jpg")
        with mock.patch.object(bake_after_index, "bake_folders", side_effect=KeyboardInterrupt):
            code, said = self.run_script(apply=False)
        self.assertEqual(130, code, said)
        self.assertTrue(said.endswith("Stopped: a dry run, nothing was written."), said)
        real = bake_after_index.bake_folders

        def pressed_during(*args, **kwargs):
            baked = real(*args, **kwargs)
            mint_identities.signal.getsignal(mint_identities.signal.SIGINT)()   # Ctrl+C, once
            return baked
        with mock.patch.object(bake_after_index, "bake_folders", side_effect=pressed_during):
            code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertIn("Stopped before folder 2 of 2", said)
        self.assertIn("all: wrote 1 identit(ies), recorded 0 from their files.", said)
        ((held, row),) = self.identities(a).values()
        self.assertEqual(held, row)
        self.assertIsNotNone(held)
        self.assertEqual({q: (None, None)}, self.identities(q))
        stamp = os.stat(a).st_mtime_ns

        code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertIn("1 photo(s) in 1 folder(s) whose row records no identity", said)
        self.assertIn("all: wrote 1 identit(ies), recorded 0 from their files.", said)
        self.assertEqual(stamp, os.stat(a).st_mtime_ns, "the first folder was written again")
        ((held, row),) = self.identities(q).values()
        self.assertTrue(str(held).startswith("xmp.did:"))
        self.assertEqual(held, row)

    def test_apply_is_refused_while_tagpup_runs_or_another_run_holds_the_library(self):
        (a,) = self.baked_before_the_mint("Harbour", "a.jpg")
        before = self.bytes_of(a)
        with mock.patch.object(mint_identities.launcher, "running", return_value=[{"pid": 4242}]):
            code, said = self.run_script(apply=True)
        self.assertEqual((1, mint_identities.APP_RUNNING % 1), (code, said))
        held = byte_lock.Lock(self.db_path + mint_identities.LOCK)
        self.assertTrue(held.acquire())
        try:
            code, said = self.run_script(apply=True)
        finally:
            held.release()
        self.assertEqual((1, mint_identities.TWO_AT_ONCE), (code, said))
        self.assertEqual(before, self.bytes_of(a))
        self.assertEqual({a: (None, None)}, self.identities(a))
        os.remove(self.db_path + mint_identities.LOCK)
        code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertFalse(os.path.exists(self.db_path + mint_identities.LOCK), "a run left its lock file behind")

    def test_a_photo_whose_file_holds_an_identity_its_row_never_recorded_has_its_row_given_it_and_its_file_left(self):
        given = "xmp.did:3f1c9a52-7d40-4e88-9b1e-5c2a7e6d0f13"
        a, b = self.baked_before_the_mint("Harbour", "a.jpg", "b.jpg", held={"b.jpg": given})
        self.assertEqual({b: (given, None)}, self.identities(b))
        before, stamp = self.bytes_of(a, b), self.stamp_of_row(b)

        code, said = self.accounted()
        self.assertEqual(0, code, said)
        self.assertIn("2 photo(s) in 1 folder(s) whose row records no identity", said)
        self.assertIn("all: 1 to mint (1 alone, 0 with their bake), 0 owed their bake alone, 1 already hold one in the "
                      "file, their row to be recorded, 0 current.", said)
        self.assertEqual(before, self.bytes_of(a, b), "a dry run wrote a file")
        self.assertEqual({b: (given, None)}, self.identities(b), "a dry run wrote a row")

        code, said = self.run_script(apply=True)
        self.assertEqual(0, code, said)
        self.assertIn("all: wrote 1 identit(ies), recorded 1 from their files.", said)
        self.assertEqual({b: (given, given)}, self.identities(b), "the row does not record the identity its file holds")
        self.assertEqual(before[b], self.bytes_of(b)[b], "a file holding its identity was written")
        self.assertEqual(stamp, self.stamp_of_row(b), "the row's stamp moved, though its file was not written")
        ((held, row),) = self.identities(a).values()
        self.assertEqual(held, row)
        self.assertTrue(str(held).startswith("xmp.did:"))

        code, said = self.run_script(apply=False)
        self.assertEqual(0, code, said)
        self.assertIn("0 photo(s) in 0 folder(s) whose row records no identity", said)
        self.assertIn("all: 0 to mint (0 alone, 0 with their bake), 0 owed their bake alone, 0 already hold one", said)


if __name__ == "__main__":
    unittest.main()
