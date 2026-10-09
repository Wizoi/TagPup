"""People by id, stage 2, "Two at once": the always-on server and a CLI run are two real PROCESSES editing one library's tree.

Every tree operation is one transaction under the library's write lock (tagpup.store.db), reading the tree inside it, so two
processes renaming two people interleave but never half-edit: after both finish, every face names a node that exists, by that
node's leaf, and the doctor's two checks find nothing. (The threads' version is tests/test_people_by_id_tree.py,
TheOwnersRapidClicks.) A person here is a node; photos are rows as the indexer records them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import SAM_I, SAM_T, TwoSams, look, write  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.store import db, faces, person_ids  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: One process: renames the node at `start` through `names` in turn, each a tree edit of its own, as the tree view makes one.
CODE = """
import sys
sys.path.insert(0, {root!r})
from tagpup.store import db, taxonomy
current = {start!r}
parent = current.rsplit("/", 1)[0]
for name in {names!r}:
    new = parent + "/" + name
    db.write_with_connection({path!r}, lambda conn, old=current, new=new: taxonomy.move_branch(conn, old, new))
    current = new
print(current)
"""


#: Process A edits the tree and HOLDS its transaction open until B is at its own edit (the pattern of finding #1046's migration test);
#: B must then read the tree after A committed -- it joins the node A renamed into, which it could not know if it had read first.
HELD = """
import os, sys, time
sys.path.insert(0, {root!r})
from tagpup.store import db, taxonomy
folder = {folder!r}

def edit(conn):
    taxonomy.move_branch(conn, {old!r}, {new!r})
    open(os.path.join(folder, "a_is_in"), "w").close()
    deadline = time.time() + 20
    while not os.path.exists(os.path.join(folder, "b_is_at_its_edit")) and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(1.0)    # B is waiting for the lock now

db.write_with_connection({path!r}, edit)
"""

WAITING = """
import os, sys, time
sys.path.insert(0, {root!r})
from tagpup.store import db, taxonomy
folder = {folder!r}
deadline = time.time() + 20
while not os.path.exists(os.path.join(folder, "a_is_in")) and time.time() < deadline:
    time.sleep(0.02)
open(os.path.join(folder, "b_is_at_its_edit"), "w").close()
started = time.time()
db.write_with_connection({path!r}, lambda conn: taxonomy.move_branch(conn, {old!r}, {new!r}))
print(round(time.time() - started, 2))
"""


class TwoProcessesEditTheTree(TwoSams, unittest.TestCase):
    def test_the_second_edit_reads_the_tree_after_the_first_committed(self):
        """Fix round 2: a tree edit read the tree before it held the write lock, so an edit another process committed in between
        was not seen -- the second moved a node into a place the first had just taken. The edit now takes the lock first
        (people.tree_edit: BEGIN IMMEDIATE) and reads inside it."""
        self.make_library()
        sam_t, sam_i = self.node(SAM_T), self.node(SAM_I)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1], self.faces[2]], sam_i))
        folder = os.path.dirname(self.path)
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        first = processes.start([sys.executable, "-c", HELD.format(root=ROOT, folder=folder, path=self.path, old=SAM_I,
                                                                  new="Family/Ingersoll/Samuel")],
                                env=env, stdout=-1, stderr=-1, text=True)
        second = processes.start([sys.executable, "-c", WAITING.format(root=ROOT, folder=folder, path=self.path, old=SAM_T,
                                                                      new="Family/Ingersoll/Samuel")],
                                 env=env, stdout=-1, stderr=-1, text=True)
        out_a, err_a = first.communicate(timeout=120)
        out_b, err_b = second.communicate(timeout=120)
        self.assertEqual(0, first.returncode, err_a)
        self.assertEqual(0, second.returncode, err_b)
        self.assertGreaterEqual(float(out_b.strip()), 0.8, "the second waited for the first's lock")
        # B saw A's rename: Family/Ingersoll/Samuel was a node, so Thackeray's Sam JOINED it (a merge), and nothing was half done.
        self.assertIsNone(self.node(SAM_T))
        self.assertEqual(sam_i, self.node("Family/Ingersoll/Samuel"))
        self.assertEqual([(sam_i, "Samuel")] * 3, [self.face(each)[:2] for each in self.faces])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([0, 0], [person_ids.out_of_step(conn, table).rows for table in person_ids.TABLES])
        finally:
            conn.close()


    def test_two_processes_renaming_two_people_leave_no_half_edit(self):
        self.make_library()
        sam_t, sam_i = self.node(SAM_T), self.node(SAM_I)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1], self.faces[2]], sam_i))
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        first = ["Samuel", "Sammy", "Sam Ingersoll", "Samson", "Sampson", "Sam One", "Sam Two", "Sam Three"]
        second = ["Sasha", "Sandy", "Sam Thackeray", "Sal", "Sabine", "Sam Four", "Sam Five", "Sam Six"]
        running = [processes.start([sys.executable, "-c", CODE.format(root=ROOT, start=start, names=names, path=self.path)],
                                   env=env, stdout=-1, stderr=-1, text=True)
                   for start, names in ((SAM_I, first), (SAM_T, second))]
        finished = []
        for each in running:
            out, err = each.communicate(timeout=240)
            self.assertEqual(0, each.returncode, err)
            finished.append(out.strip())
        self.assertEqual(["Family/Ingersoll/" + first[-1], "Family/Thackeray/" + second[-1]], finished)
        # Each node is where its process left it, with the id it began with; the faces follow by id and by the cache of the name.
        self.assertEqual(sam_i, self.node(finished[0]))
        self.assertEqual(sam_t, self.node(finished[1]))
        self.assertEqual([(sam_t, second[-1]), (sam_i, first[-1]), (sam_i, first[-1])],
                         [self.face(each)[:2] for each in self.faces])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([0, 0], [person_ids.out_of_step(conn, table).rows for table in person_ids.TABLES])
            self.assertEqual([], conn.execute(
                "SELECT id FROM faces WHERE tag_id IS NOT NULL AND tag_id NOT IN (SELECT id FROM tag_taxonomy)").fetchall())
        finally:
            conn.close()

    def test_a_rename_in_one_process_and_a_merge_in_another_end_in_one_whole_state(self):
        self.make_library()
        sam_t, sam_i = self.node(SAM_T), self.node(SAM_I)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1], self.faces[2]], sam_i))
        merge = ("import sys; sys.path.insert(0, %r); from tagpup.store import db, taxonomy; "
                 "db.write_with_connection(%r, lambda conn: taxonomy.move_branch(conn, %r, %r)); print('merged')"
                 % (ROOT, self.path, SAM_T, SAM_I))
        rename = CODE.format(root=ROOT, start=SAM_I, names=["Samuel"], path=self.path)
        env = dict(os.environ, TAGPUP_HOME=self.home.root)
        running = [processes.start([sys.executable, "-c", code], env=env, stdout=-1, stderr=-1, text=True) for code in (merge, rename)]
        for each in running:
            out, err = each.communicate(timeout=240)
            self.assertEqual(0, each.returncode, err)
        # Whichever went first: the three faces name nodes that exist, by their leaf, and nothing is out of step.
        rows = look(self.path, "SELECT f.id, f.tag_id, f.name, t.name FROM faces f LEFT JOIN tag_taxonomy t ON t.id = f.tag_id"
                               " WHERE f.name IS NOT NULL ORDER BY f.id")
        self.assertEqual(3, len(rows))
        for face_id, tag_id, name, leaf in rows:
            self.assertIsNotNone(tag_id, "face %d is linked" % face_id)
            self.assertEqual(leaf, name, "face %d names its node's leaf" % face_id)
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            self.assertEqual([0, 0], [person_ids.out_of_step(conn, table).rows for table in person_ids.TABLES])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
