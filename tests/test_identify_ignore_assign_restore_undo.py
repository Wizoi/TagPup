"""Scenario P7: a person works down Identify Faces' Unknown Faces grid with Ignore cluster, Assign, Restore and the Undo bar.

Every step keeps the grid in place (the cached payload loses the cards and is not built again: that costs the better part of a minute
on the 225,000-face library) and gives the counts a build from scratch gives; the Undo bar then puts faces, the photos' person tags
and photo_people back exactly. Real writers all the way: the bulk routes (tagpup.services.face_assignment), History's undo route, the
grid cache of tagpup.web.tuner_routes. ExifTool is a table of files; faces are rows with a vector, as the indexer stores them.

Lifted: the granular GridCache tests of the old tests/test_identify_faces_hot_paths.py (the warm grid after exclude, assign and
restore; the capped tail rule; a group of one is not a group; an entry another write left behind is not revived; one person's edit
leaves another's grid). They cited no finding numbers.
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import InStep  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402
import tuner_client as grids  # noqa: E402

from tagpup.jobs import identify as identify_jobs  # noqa: E402
from tagpup.services import identify as identify_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

KEY = "matches:Unknown Faces"
CAP = 6
GRID = "/library/api/unmatched-faces/person-matches?name="


def vector(seed, group=None):
    """A unit 512-vector; with `group`, a small step from that group's, so they cluster."""
    own = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    own /= np.linalg.norm(own)
    if group is not None:
        centre = np.random.default_rng(group).standard_normal(512).astype(np.float32)
        own = centre / np.linalg.norm(centre) + own * 0.02
    return (own / np.linalg.norm(own)).astype(np.float32).tobytes()


class IgnoreAssignRestoreUndo(InStep):
    def setUp(self):
        super().setUp()
        for module in (identify_service, identify_jobs):
            patch = mock.patch.object(module, "UNCLUSTERED_LIMIT", CAP)
            patch.start()
            self.addCleanup(patch.stop)
        grids.forget(self.path)
        self.addCleanup(grids.forget, self.path)
        self.seeds = iter(range(1000, 9000))
        self.cluster_a = self.faces_of("a", 6, group=100)
        self.cluster_b = self.faces_of("b", 4, group=200)
        self.loose = self.faces_of("loose", 12)

    @property
    def cache(self):
        """The library's grid cache; forgetting the library makes a new one."""
        return grids.grid_cache(self.path)

    def faces_of(self, prefix, count, group=None):
        return [self.face(self.held("%s_%d.jpg" % (prefix, n)), embedding=vector(next(self.seeds), group), prob=0.99)
                for n in range(count)]

    def grid(self, name="Unknown%20Faces"):
        return self.tuner_client.get(GRID + name).get_json()

    @staticmethod
    def shape(grid):
        """The clusters, how many faces wait unclustered, how many of them are shown, and whether more wait behind."""
        groups = {}
        for card in grid["faces"]:
            groups.setdefault(card["cluster_id"], set()).add(card["id"])
        clusters = {frozenset(ids) for cid, ids in groups.items() if cid != -1}
        return clusters, grid["unclustered_total"], len(groups.get(-1, ())), grid["has_more"]

    def agrees(self, why):
        """The grid the server serves is the one a build from scratch gives (which this leaves cached): the same clusters and
        counts, though a warm grid may show a survivor more than the cap."""
        served = self.shape(self.grid())
        grids.forget(self.path)
        built = self.shape(self.grid())
        self.assertEqual((built[:2], built[3]), (served[:2], served[3]), why)

    def kept(self, why):
        entry = self.cache.entry(KEY)
        self.assertIsNotNone(entry, why)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual(store_faces.fingerprint(conn), entry["fingerprint"], why + ": left stamped with the old table")
        finally:
            conn.close()

    def write(self, route, face_ids, **body):
        reply = self.tuner_post(route, dict(body, face_ids=face_ids))
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def ignore(self, face_ids):
        return self.write("faces/exclude", face_ids, reason="ignored cluster", bulk=True)

    def assign(self, face_ids):
        return self.write("faces/match-bulk", face_ids, person_name=WREN)

    def undo(self, written):
        """The Undo bar (web/tuner/assign.js): the journal's undo of the change, then Restore for the faces it did not hold."""
        if written["change"]:
            reply = self.client.post("/library/api/history/%d/undo" % written["change"], json={"apply": True})
            self.assertEqual(200, reply.status_code, reply.get_json())
        if written.get("plain"):
            self.write("faces/restore", written["plain"])

    def everything(self):
        """Faces, photo_people and each photo's tags: what an undo must put back exactly."""
        return (self.look("SELECT id, name, name_source, excluded, excluded_reason, tag_id FROM faces ORDER BY id"),
                self.look("SELECT photo_id, position, name, source FROM photo_people ORDER BY photo_id, position"),
                {path: sorted(self.tags(path)) for (path,) in self.look("SELECT path FROM photos ORDER BY id")})

    def test_the_grid_stays_in_place_through_ignore_assign_restore_and_the_undo_bar(self):
        a, b = frozenset(self.cluster_a), frozenset(self.cluster_b)
        self.assertEqual(({a, b}, 12, CAP, True), self.shape(self.grid()))
        self.grid(ODA.replace(" ", "%20"))      # another person's grid, which none of this touches
        others = [key for key in self.cache.keys() if key.startswith("matches:") and key != KEY]
        self.assertTrue(others, "the other person's grid was not cached")

        # A cluster keeps two faces: still a cluster. One more goes: the survivor is no group, it joins the loose pile.
        self.ignore(self.cluster_b[:2])
        self.kept("ignoring part of a cluster threw the grid away")
        self.assertEqual(({a, frozenset(self.cluster_b[2:])}, 12), self.shape(self.grid())[:2])
        self.ignore(self.cluster_b[2:3])
        self.kept("the last of a cluster going threw the grid away")
        self.assertEqual(({a}, 13), self.shape(self.grid())[:2], "the survivor still flew a cluster's colours")
        for key in others:
            self.assertEqual(self.cache.entry(KEY)["fingerprint"], self.cache.entry(key)["fingerprint"],
                             "another person's grid was left stale by this edit")
        self.agrees("a stranded survivor left a different grid than a build")

        # Restore: a face coming back has no cluster to belong to; the grid is built again, not guessed.
        self.write("faces/restore", self.cluster_b[:3])
        self.assertEqual({a, b}, self.shape(self.grid())[0], "the restored faces never came back to the grid")

        # The indexer adds a face while the grid is cached: a later write must not revive the grid built before it.
        self.grid()
        self.faces_of("late", 1)
        before = self.everything()
        change_a = self.ignore(self.cluster_a)
        self.assertEqual(({b}, 13), self.shape(self.grid())[:2], "a grid built before the indexer's face was revived")
        self.agrees("the grid was carried over a write it was not told of")

        # Ignoring a cluster leaves the capped tail as it was; one loose face less thins it a little: the grid is kept.
        shown = [card["id"] for card in self.grid()["faces"] if card["cluster_id"] == -1]
        change_one = self.assign(shown[:1])
        self.kept("assigning one loose face threw the capped grid away")
        self.assertEqual(CAP - 1, self.shape(self.grid())[2])
        self.agrees("thinning the tail left a different grid than a build")

        # Wearing the tail down past half the cap builds again, so the faces behind it come forward.
        shown = [card["id"] for card in self.grid()["faces"] if card["cluster_id"] == -1]
        change_many = self.assign(shown[:4])
        self.assertEqual(CAP, self.shape(self.grid())[2], "the visible tail wore down and the faces behind it stayed unreachable")
        self.agrees("a worn tail left a different grid than a build")

        # The Undo bar, newest first: each leaves the grid as a build would, and in the end everything is as it was.
        for change in (change_many, change_one, change_a):
            self.undo(change)
            self.agrees("an undo left a grid that is not the library's")
        self.assertEqual(before, self.everything())
        self.assertEqual(({a, b}, 13), self.shape(self.grid())[:2])


if __name__ == "__main__":
    unittest.main()
