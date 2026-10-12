"""The replies the pages' batch strip reads (web/common/batch-strip.js), made by the real routes and written to
tests/frontend/fixtures/batches.json for the page's test (tests/frontend/batch-strip.test.mjs) to answer from, so the page is held to
what the server sends and not to a reply written by hand (docs/OWNERS.md, contract 22). Volatile numbers (tickets, change ids, clocks)
are renumbered, so the file is the same on every run. Fictional names only.
"""
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402
from test_batches import ANOTHER, in_thread, wait_until  # noqa: E402

from tagpup.services import batches  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "fixtures", "batches.json")


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
        wait_until(lambda: batches.running_here() == 2, "the second batch queues")
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
            wait_until(lambda: batches.running_here() == n, "batch %d is in line" % n)
        full = self.post({"op": "tags", "selection": T.ALL, "params": {"add": ["Trips/Coast"]}})
        found["queue_full"] = {"status": full.status_code, "body": full.get_json()}
        release.set()
        for holder in holders:
            holder.join(30)
        # One photo over the cap.
        over = self.post({"op": "tags", "selection": {"ids": list(range(1, batches.MAX_BATCH + 2))}, "params": {"add": ["Trips/Coast"]}})
        found["over_the_cap"] = {"status": over.status_code, "body": over.get_json()}

        self.assertEqual(["running", "queued"], [row["state"] for row in found["running_and_queued"]])
        self.assertEqual(400, found["cancelled_before_it_began"]["status"])
        self.assertEqual(404, found["cancel_of_a_batch_that_is_gone"]["status"])
        self.assertIn("Stopped as asked after 25 of 60 photos", found["stopped_reply"]["errors"][0]["why"])
        self.assertEqual([False], [row["here"] for row in found["another_program"]])
        self.assertEqual(409, found["queue_full"]["status"])
        self.assertEqual(400, found["over_the_cap"]["status"])
        text = "{\n" + ",\n".join("%s: %s" % (json.dumps(key), json.dumps(found[key], sort_keys=True)) for key in sorted(found)) + "\n}\n"
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        if not os.path.exists(FIXTURE) or open(FIXTURE, encoding="utf-8").read() != text:
            with open(FIXTURE, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)


if __name__ == "__main__":
    unittest.main()
