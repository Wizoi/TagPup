"""Time a bulk edit by photo id the way the owner would meet it: real JPEGs, the real ExifTool, in a sandbox.

The numbers in docs/ARCHITECTURE.md, "Phase 9d-1", come from this. In a library of its own under a temporary TAGPUP_HOME
(made here and deleted afterwards; nothing in data/ is touched, no port is opened -- the app is called in this process through
Flask's test client), it makes N small JPEGs with a Date Taken and one keyword, records them as the indexer records a read, and
measures:

  (a) a tags job over all N photos (add one tag, take one off): wall time and photos a second, chunk by chunk;
  (b) a time-shift job over the same: the same;
  (c) while (a) runs, single-photo saves (POST /api/photo/save-metadata, what the details panel sends) made at 300 ms
      intervals: how long each waits, against the length of a chunk -- they must wait for the chunk being written and no more;
  (d) the status request while it runs: its cost, polled every 20 ms;
  (e) a second start while one runs (409), and the cancel: how long after the click the job has stopped.

    .venv/Scripts/python.exe scripts/measure_bulk_jobs.py                    # the plan
    .venv/Scripts/python.exe scripts/measure_bulk_jobs.py --run --photos 500

The tally and the resolver at the real library's scale are read on photo_index itself, read-only, by `--scale <the library
file>` (counts and milliseconds are printed, no name).
"""
import argparse
import os
import statistics
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from sandbox import enter, remove_sandbox  # noqa: E402


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def make_photos(folder, count, exiftool):
    from PIL import Image

    from tagpup.files.exiftool_session import ExifToolSession
    os.makedirs(folder)
    made = []
    for n in range(count):
        path = os.path.join(folder, "IMG_%04d.jpg" % n)
        Image.new("RGB", (64, 48), (n % 255, 90, 120)).save(path, "JPEG")
        made.append(path)
    with ExifToolSession(executable=exiftool) as et:
        for start in range(0, count, 100):
            et.set_tags(made[start:start + 100], tags={"EXIF:DateTimeOriginal": "2024:07:04 10:00:00",
                                                       "XMP:Subject": ["Old/Stuff"]}, params=["-overwrite_original"])
    return made


def record(library_path, made):
    from tagpup.files.metadata import MetadataExtractor
    from tagpup.store import db, photos, taxonomy
    conn = db.connect(library_path)
    try:
        for tag in ("Old/Stuff", "Trips/Coast"):
            taxonomy.add_path(conn, tag)
        for path in made:
            record_ = MetadataExtractor()._structure(path, {"SourceFile": path, "EXIF:DateTimeOriginal": "2024:07:04 10:00:00",
                                                           "XMP:Subject": ["Old/Stuff"]}, None)
            photos.record_indexed(conn, path, record_)
        conn.commit()
    finally:
        conn.close()


def run(photos, exiftool):
    from unittest import mock

    from tagpup.core.library import Library
    from tagpup.jobs import bulk_edits
    from tagpup.services import libraries
    from tagpup.web import app as web

    home = tempfile.mkdtemp(prefix="tagpup_measure_bulk_")
    os.makedirs(os.path.join(home, "data"))
    enter(home)
    try:
        path = os.path.join(home, "data", "measured.db")
        libraries.create(path)
        library = Library(path)
        print("making %d JPEGs and their rows ..." % photos)
        made = make_photos(os.path.join(home, "Pictures", "Coast"), photos, exiftool)
        record(path, made)
        app = web.create_app("tagpup", startup=library)
        app.testing = True
        client = app.test_client()
        base = "/measured/api"
        with mock.patch("tagpup.web.state.exiftool", return_value=exiftool):
            for label, op, params in (("(a) tags", "tags", {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]}),
                                      ("(b) time shift", "time_shift", {"minutes": 90})):
                started = time.perf_counter()
                reply = client.post(base + "/library/bulk/start", json={"op": op, "selection": {"source": {"kind": "all"}},
                                                                        "params": params}).get_json()
                handle = reply["job"]
                job = bulk_edits._held(library)[handle]
                polls, saves, ticks = [], [], []
                stop = threading.Event()

                def save_now(target):
                    began = time.perf_counter()
                    client.post(base + "/photo/save-metadata", json={"path": target, "title": "", "tags": ["Saved By Hand"]})
                    saves.append((time.perf_counter() - began) * 1000)

                last_save = 0.0
                while job.thread.is_alive():
                    began = time.perf_counter()
                    seen = client.get(base + "/library/bulk/status", query_string={"job": handle}).get_json()
                    polls.append((time.perf_counter() - began) * 1000)
                    ticks.append((time.perf_counter(), seen["done"]))
                    now = time.perf_counter()
                    if label.startswith("(a)") and 10 <= seen["done"] <= photos - 40 and now - last_save > 0.3:
                        last_save = now
                        thread = threading.Thread(target=save_now, args=(made[len(saves) % 5],))
                        thread.start()
                        thread.join(30)
                    stop.wait(0.02)
                elapsed = time.perf_counter() - started
                done = client.get(base + "/library/bulk/status", query_string={"job": handle}).get_json()
                chunks = max(1, -(-photos // bulk_edits.CHUNK))
                print("\n%s: %d photos, %s, %.1f s: %.1f photos a second; %d changed, %d errors; a chunk of %d: %.0f ms"
                      % (label, photos, done["state"], elapsed, photos / elapsed, done["changed"], done["error_count"],
                         bulk_edits.CHUNK, elapsed / chunks * 1000))
                print("  status polled %d times: median %.2f ms, p95 %.2f ms, longest %.2f ms"
                      % (len(polls), statistics.median(polls), percentile(polls, 0.95), max(polls)))
                if saves:
                    print("  %d single-photo saves during the job: median %.0f ms, longest %.0f ms (a chunk is %.0f ms)"
                          % (len(saves), statistics.median(saves), max(saves), elapsed / chunks * 1000))
        # (e) one at a time, and cancel
        reply = client.post(base + "/library/bulk/start", json={"op": "tags", "selection": {"source": {"kind": "all"}},
                                                                "params": {"add": ["Trips/Coast"], "remove": ["Old/Stuff"]}})
        job = bulk_edits._held(library)[reply.get_json()["job"]]
        with mock.patch("tagpup.web.state.exiftool", return_value=exiftool):
            second = client.post(base + "/library/bulk/start", json={"op": "tags", "selection": {"source": {"kind": "all"}},
                                                                     "params": {"add": ["Trips/Lakes"]}})
        began = time.perf_counter()
        client.post(base + "/library/bulk/cancel", json={"job": job.handle})
        job.thread.join(60)
        print("\n(e) a second start while one runs: %d; cancel to stopped: %.0f ms; state %s, %d of %d done"
              % (second.status_code, (time.perf_counter() - began) * 1000, job.state, job.done, job.total))
    finally:
        remove_sandbox(home)


def scale(path):
    """Read-only on the library at `path` (photo_index): the resolver and the tally at its size."""
    from tagpup.store import db
    from tagpup.store import library_view as store
    if not os.path.exists(path):
        print("no library at that path")
        return

    def connect():
        return db.connect(db.readonly_uri(path), uri=True)

    def timed(label, work):
        took = []
        for _ in range(3):
            began = time.perf_counter()
            out = work()
            took.append((time.perf_counter() - began) * 1000)
        print("%-46s median %5.0f ms (%s)" % (label, statistics.median(took), ", ".join("%.0f" % each for each in took)))
        return out

    def resolve():
        conn = connect()
        try:
            return store.source_ids(conn, store.Source("all"), 200000)
        finally:
            conn.close()

    def tally(ids, source, excluded=()):
        conn = connect()
        try:
            return store.tally(conn, ids, source, excluded)
        finally:
            conn.close()

    ids, total = timed("resolve the whole library (one transaction)", resolve)
    print("  %d ids" % len(ids))
    found = timed("tally the whole library as a source", lambda: tally(None, store.Source("all")))
    print("  total %d, %d tags, %d people" % (found["total"], len(found["tags"]), len(found["people"])))
    timed("tally the whole library minus 20,000", lambda: tally(None, store.Source("all"), ids[:20000]))
    timed("tally 20,000 ids", lambda: tally(ids[:20000], None))
    timed("tally 68,000 ids", lambda: tally(ids, None))
    timed("tally one id", lambda: tally(ids[:1], None))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="make the sandbox and measure (else only the plan is printed)")
    parser.add_argument("--photos", type=int, default=500)
    parser.add_argument("--scale", metavar="LIBRARY", help="read-only: the resolver and the tally on the library file named "
                                                         "(photo_index.db); counts and milliseconds are printed")
    args = parser.parse_args()
    if args.scale:
        return scale(args.scale)
    if not args.run:
        print(__doc__)
        return None
    from tagpup import config as tagpup_config
    exiftool = tagpup_config.exiftool_path("")
    if not os.path.exists(exiftool):
        sys.exit("ExifTool is not installed here: %s" % exiftool)
    return run(args.photos, exiftool)


if __name__ == "__main__":
    main()
