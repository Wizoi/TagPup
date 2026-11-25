"""The replies the pages' batch strip reads (web/common/batch-strip.js), made by the real routes and written to
tests/frontend/fixtures/batches.json for the pages' tests (tests/frontend/batch-strip.test.mjs; a selection's Delete, its question
and its reply: library-header-strip-toolbar.test.mjs) to answer from, so the page is held to
what the server sends and not to a reply written by hand (docs/OWNERS.md, contract 22); a reply that differs from
the file fails, naming the command that writes it again (page_fixtures). Volatile numbers (tickets, change ids, clocks)
are renumbered, so the file is the same on every run. Fictional names only.
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_fixtures  # noqa: E402
import test_bulk_edits as T  # noqa: E402
from test_batches import ANOTHER, in_thread, wait_until  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import exiftool_session, recycle_bin  # noqa: E402
from tagpup.services import batches  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

FIXTURE = page_fixtures.path_of("batches.json")
#: Where a copy from a share restores to: the sandbox's Downloads differs run to run, so the file holds a fixed fictional one.
RESTORES_TO = "C:\\Users\\wren\\Downloads\\TagPup deleted from shares"
FULL = {"TAGPUP_RECYCLE_BIN": "10,10,0"}     # a Bin of 10 MB holding 10 MB: full


def steady(rows):
    """`rows` with the numbers that differ from run to run replaced by small fixed ones."""
    tickets, changes = {}, {}
    out = []
    for row in rows:
        row = dict(row)
        if row["ticket"] is not None:
            row["ticket"] = tickets.setdefault(row["ticket"], len(tickets) + 1)
        if row["id"] is not None:
            row["id"] = changes.setdefault(row["id"], 10 + len(changes))
        if row["started"] is not None:
            row["started"] = 1000.0
        out.append(row)
    return out


class RepliesForTheStrip(T.Bulk):
    def get(self, path):
        return self.client.get(path).get_json()

    def deletes_by_path(self):
        """Three photos of one folder deleted as Organize names them, by path (selection.read's `paths`): the question
        and the reply, to the Recycle Bin, and with the Bin full, for good once confirmed; and two photos with a file copied
        in and not indexed yet, which the question counts `missing` and the delete leaves. The Bin is the test's; nothing
        reaches the owner's."""
        found, binned = {}, []
        fresh = os.path.join(self.vl.pictures, "2024 A", "copied_in.jpg")
        T.make_jpeg(fresh, (10, 20, 30), (16, 16))

        def recycle(path):
            binned.append(path)
            os.remove(path)
            return True
        recycle_bin.forget_bin()
        self.addCleanup(recycle_bin.forget_bin)
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            for suffix, numbers, extra, bin_state, confirm in (("", (0, 2, 4), [], {}, {}),
                                                              ("_for_good", (6, 8, 10), [], FULL, {"for_good": True}),
                                                              ("_with_missing", (12, 14), [fresh], {}, {})):
                chosen = {"paths": [os.path.join(self.vl.pictures, "2024 A", "p%03d.jpg" % n) for n in numbers] + extra}
                recycle_bin.forget_bin()
                with mock.patch.dict(os.environ, bin_state):
                    asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": chosen})
                    self.assertEqual(200, asked.status_code, asked.get_json())
                    asked = asked.get_json()
                    done = self.post({"op": "delete", "selection": chosen, "params": {"token": asked["token"], **confirm}})
                self.assertEqual(200, done.status_code, done.get_json())
                found["delete_check" + suffix] = {**asked, "restores_to": RESTORES_TO}
                found["deleted" + suffix] = done.get_json()
        recycle_bin.forget_bin()
        self.assertEqual((3, 1, 0), tuple(found["delete_check"][key] for key in ("total", "folders", "bin_full")))
        self.assertEqual((3, 3), (found["delete_check_for_good"]["for_good"]["count"], found["delete_check_for_good"]["bin_full"]))
        self.assertEqual((3, 0, 0), tuple(found["deleted"][key] for key in ("changed", "for_good", "error_count")))
        self.assertEqual((3, 3, 0), tuple(found["deleted_for_good"][key] for key in ("changed", "for_good", "error_count")))
        self.assertEqual((2, 1), tuple(found["delete_check_with_missing"][key] for key in ("total", "missing")))
        self.assertEqual((2, 3, 1, 0), tuple(found["deleted_with_missing"][key] for key in ("changed", "requested", "missing", "error_count")))
        self.assertTrue(os.path.exists(fresh), "the file the library holds no row of is left")
        self.assertEqual(5, len(binned), "the first three and the last two went to the Bin, the second three for good")
        return found

    def test_the_replies_the_strip_reads_are_written_for_the_page_test(self):
        found = {}
        # One running (held at its first write) and one waiting behind it.
        gate = T.Gate(1)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        body = {"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}}
        first, first_reply = in_thread(lambda: self.post(body, client=self.app.test_client()).get_json())
        self.assertTrue(gate.reached.wait(30))
        second, second_reply = in_thread(lambda: self.post(
            {**body, "params": {"add": ["Trips/Lakes"]}}, client=self.app.test_client()))
        wait_until(lambda: batches.running_here() == 2, what="the second batch queues")
        listed = self.get("/api/activity/batches")["batches"]
        found["running_and_queued"] = steady(listed)
        queued = [row for row in listed if row["state"] == "queued"][0]
        cancel = {"library": self.library.name, "ticket": queued["ticket"]}
        found["cancel_answer"] = self.client.post("/api/activity/batches/cancel", json=cancel).get_json()
        second.join(30)
        reply = second_reply[0]
        found["cancelled_before_it_began"] = {"status": reply.status_code, "body": reply.get_json()}
        gone = self.client.post("/api/activity/batches/cancel", json=cancel)
        found["cancel_of_a_batch_that_is_gone"] = {"status": gone.status_code, "body": gone.get_json()}
        gate.release.set()
        first.join(60)
        self.assertEqual(60, first_reply[0]["changed"])
        self.assertEqual([], self.get("/api/activity/batches")["batches"])
        # A batch asked to stop at its chunk: the reply of its own request.
        tenth = self.files.writes + 10
        self.files.on_write = lambda _path, n: n == tenth and self.app.test_client().post("/api/activity/batches/cancel", json={
            "library": self.library.name, "ticket": self.get("/api/activity/batches")["batches"][0]["ticket"]})
        found["stopped_reply"] = self.tags_edit(add=["Trips/Lakes"])
        self.assertEqual(35, len(found["stopped_reply"]["errors"]))
        found["stopped_reply"]["errors"] = sorted(found["stopped_reply"]["errors"], key=lambda each: each["id"])[:2]    # the order is a set's
        self.files.on_write = None
        # The change of another program, read from the journal.
        untagged = [each for n, each in enumerate(self.ids) if n % 3][:30]
        change_id, _rows = file_journal.plan(self.library.path, "bulk tags", [
            {"photo_id": each, "path": self.path(each), "before": {"XMP:Subject": []}, "after": {"XMP:Subject": ["Trips/Coast"]}}
            for each in untagged])
        db.write_with_connection(self.library.path, lambda conn: conn.execute(
            "UPDATE changes SET owner = ? WHERE id = ?", (ANOTHER, change_id)), label="test: whose change")
        found["another_program"] = steady(self.get("/api/activity/batches")["batches"])
        # Nine in line: the next is refused, saying how many are ahead.
        release, holders = threading.Event(), []
        self.addCleanup(release.set)

        def hold():
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                release.wait(30)
        for n in range(1, batches.MOST_WAITING + 2):
            holder = threading.Thread(target=hold)
            holder.start()
            holders.append(holder)
            wait_until(lambda: batches.running_here() == n, what="batch %d is in line" % n)
        full = self.post({"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}})
        found["queue_full"] = {"status": full.status_code, "body": full.get_json()}
        release.set()
        for holder in holders:
            holder.join(30)
        # One photo over the cap.
        over = self.post({"op": "tags", "selection": {"ids": list(range(1, batches.MAX_BATCH + 2))}, "params": {"add": ["Trips/Coast"]}})
        found["over_the_cap"] = {"status": over.status_code, "body": over.get_json()}
        # ExifTool stops answering at the eleventh file's read before its write: a stop, said by its own sentence.
        carried, before = [], dict(self.files.reads)

        def stalls(path, n):
            # The write's own read of a file; the first was the plan's.
            if n == before.get(paths.key(path), 0) + 2 and len(carried) < 11:
                carried.append(path)
                if len(carried) == 11:
                    raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")
        self.files.on_read = stalls
        found["share_stopped_reply"] = self.tags_edit(add=["Old/Stuff"])
        self.files.on_read = None
        found["share_stopped_reply"]["errors"] = sorted(found["share_stopped_reply"]["errors"], key=lambda each: each["id"])[:2]
        # Organize's delete of a folder's selection, by path: the question and the reply, to the Bin and for good.
        found.update(self.deletes_by_path())

        self.assertEqual(["running", "queued"], [row["state"] for row in found["running_and_queued"]])
        self.assertEqual(400, found["cancelled_before_it_began"]["status"])
        self.assertEqual(404, found["cancel_of_a_batch_that_is_gone"]["status"])
        self.assertIn("Stopped as asked after 25 of 60 photos", found["stopped_reply"]["errors"][0]["why"])
        self.assertIn("ExifTool stopped answering after 10 of", found["share_stopped_reply"]["errors"][0]["why"])
        self.assertEqual([False], [row["here"] for row in found["another_program"]])
        self.assertEqual(409, found["queue_full"]["status"])
        self.assertEqual(400, found["over_the_cap"]["status"])
        text = "{\n" + ",\n".join("%s: %s" % (json.dumps(key), json.dumps(found[key], sort_keys=True)) for key in sorted(found)) + "\n}\n"
        page_fixtures.hold_to(self, FIXTURE, text, "tests.test_batch_strip_replies", "the batch strip")


if __name__ == "__main__":
    unittest.main()
