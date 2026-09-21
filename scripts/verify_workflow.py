"""End-to-end verification of the TagPup / TagTuner workflow, in isolation.

Runs the whole loop against a throwaway copy of a database, on its own ports, so it
cannot disturb a live session and a live session cannot confound its results.

Three things keep it out of the way:

  * It works on a copy. Nothing it does reaches the real database.
  * It refuses to start while another indexer is running, because indexing is
    GPU-bound and two jobs on one GPU make each other crawl.
  * It kills every subprocess it started before exiting. The server spawns the
    indexer with Popen, which on Windows outlives its parent -- an earlier run of
    this script left an indexer writing to a live database for twenty minutes.

Usage:
    .venv\\Scripts\\python scripts/verify_workflow.py --source data/kr-track.db \\
        --folder "D:\\path\\to\\an\\unindexed\\folder"

    --keep      leave the copy behind for inspection
    --no-index  skip the indexing stage (fast; assumes the folder is already there)
"""
import argparse
import json
import os
import shutil
import sqlite3
try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TUNER_PORT, TAGPUP_PORT = 9401, 9402


class Report:
    def __init__(self):
        self.failures = []
        self.passes = 0

    def check(self, label, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""),
              flush=True)
        if ok:
            self.passes += 1
        else:
            self.failures.append(f"{label} ({detail})" if detail else label)
        return ok

    def summary(self):
        print(f"\n{self.passes} passed, {len(self.failures)} failed")
        for f in self.failures:
            print(f"   FAILED: {f}")
        return 0 if not self.failures else 1


def indexers_running():
    """PIDs of any tagpup_cli index process, ours or anyone's."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*tagpup_cli.py*index*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            stderr=subprocess.DEVNULL, creationflags=0x08000000,
        ).decode(errors="ignore")
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def kill_tree(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=0x08000000)
    except Exception:
        pass


def call(port, path, body=None, timeout=1800):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw.decode())
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/kr-track.db")
    ap.add_argument("--folder", required=False)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-index", action="store_true")
    args = ap.parse_args()

    # Refuse to compete for the GPU. An indexer already running means a real session
    # is mid-job; starting another makes both crawl and the results meaningless.
    busy = indexers_running()
    if busy and not args.no_index:
        print(f"An indexer is already running (PID {', '.join(map(str, busy))}).")
        print("Indexing is GPU-bound; running a second job would slow both and make")
        print("these timings meaningless. Wait for it to finish, or pass --no-index.")
        return 2

    # test_ prefix keeps the copy out of the database selector in both interfaces.
    work_db = os.path.join("data", "test_verify_workflow.db")
    for leftover in (work_db, work_db.replace(".db", "_taxonomy.json")):
        if os.path.exists(leftover):
            os.remove(leftover)

    src = tagpup_db.connect(f"file:{args.source}?mode=ro", uri=True, timeout=60.0)
    dst = tagpup_db.connect(work_db, timeout=60.0)
    src.backup(dst)
    dst.close()
    src.close()
    tax = args.source.replace(".db", "_taxonomy.json")
    if os.path.exists(tax):
        shutil.copyfile(tax, work_db.replace(".db", "_taxonomy.json"))
    print(f"working on a copy: {work_db} ({os.path.getsize(work_db)/1e6:.1f} MB)\n")

    from tuner_server import start_server as start_tuner
    from tagpup_server import start_server as start_tagpup

    threading.Thread(target=start_tuner, kwargs={
        "port": TUNER_PORT, "db_path": work_db, "gui_dir": "gui"}, daemon=True).start()
    threading.Thread(target=start_tagpup, kwargs={
        "port": TAGPUP_PORT, "db_path": work_db, "gui_dir": "gui_tagpup"}, daemon=True).start()
    time.sleep(3.0)

    started_before = set(indexers_running())
    report = Report()
    try:
        run_checks(report, work_db, args)
    finally:
        # Anything this run spawned dies with it, even on an exception.
        for pid in set(indexers_running()) - started_before:
            print(f"  cleaning up indexer PID {pid}")
            kill_tree(pid)
        if not args.keep:
            # The servers run in daemon threads that hold their connections open for as
            # long as this process lives, so on Windows the copy usually cannot be
            # deleted from inside it. That is why the next run clears it on startup.
            for leftover in (work_db, work_db.replace(".db", "_taxonomy.json")):
                try:
                    os.remove(leftover)
                except OSError:
                    print(f"  ({leftover} is still open; the next run will clear it)")
        else:
            print(f"\ncopy kept at {work_db}")

    return report.summary()


def db(work_db, query, args=()):
    conn = tagpup_db.connect(f"file:{work_db}?mode=ro", uri=True)
    try:
        row = conn.execute(query, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def db_all(work_db, query, args=()):
    conn = tagpup_db.connect(f"file:{work_db}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(query, args).fetchall()]
    finally:
        conn.close()


def run_checks(report, work_db, args):
    photos0 = db(work_db, "SELECT COUNT(*) FROM photos")
    faces0 = db(work_db, "SELECT COUNT(*) FROM faces")
    named0 = db(work_db, "SELECT COUNT(*) FROM faces WHERE name IS NOT NULL")
    manual0 = db(work_db, "SELECT COUNT(*) FROM faces WHERE name_source = 'manual'")
    print(f"baseline: photos={photos0} faces={faces0} named={named0} manual={manual0}\n")

    folder = args.folder
    if folder and not args.no_index:
        print("TagTuner: bring a folder into the index")
        t0 = time.time()
        status, body = call(TUNER_PORT, "/api/folder/index-start", {"folder_path": folder})
        report.check("index-start accepted", status == 200 and isinstance(body, dict)
                     and body.get("success"), str(body)[:70])

        q = urllib.parse.quote(folder)
        final = {}
        deadline = time.time() + 3600
        while time.time() < deadline:
            _, final = call(TUNER_PORT, f"/api/folder/index-status?path={q}")
            if isinstance(final, dict) and final.get("status") in ("completed", "failed"):
                break
            time.sleep(2)
        elapsed = time.time() - t0
        report.check("indexing completed",
                     isinstance(final, dict) and final.get("status") == "completed",
                     str(final.get("message", ""))[:60] if isinstance(final, dict) else str(final))

        photos1 = db(work_db, "SELECT COUNT(*) FROM photos")
        faces1 = db(work_db, "SELECT COUNT(*) FROM faces")
        added = photos1 - photos0
        report.check("new photos indexed", added > 0, f"{photos0} -> {photos1}")
        report.check("faces detected in them", faces1 > faces0, f"{faces0} -> {faces1}")
        if added > 0:
            print(f"        throughput: {elapsed/added:.2f}s per photo ({added} in {elapsed:.0f}s)")
        report.check("untagged photos were indexed",
                     db(work_db, "SELECT COUNT(*) FROM photos WHERE tags IS NULL OR tags = '[]'") > 0)
        MANUAL_Q = "SELECT COUNT(*) FROM faces WHERE name_source = 'manual'"
        NAMED_Q = "SELECT COUNT(*) FROM faces WHERE name IS NOT NULL"
        manual1, named1 = db(work_db, MANUAL_Q), db(work_db, NAMED_Q)
        report.check("indexing preserved every manual name", manual1 == manual0,
                     f"manual {manual0} -> {manual1}")
        report.check("indexing assigned no names by itself", named1 == named0,
                     f"named {named0} -> {named1}")

    print("\nTagTuner: the identify queue")
    t0 = time.time()
    _, queue = call(TUNER_PORT, "/api/unmatched-faces/people")
    cold_ms = (time.time() - t0) * 1000
    report.check("queue is populated", isinstance(queue, list) and len(queue) > 0,
                 f"{len(queue) if isinstance(queue, list) else '?'} entries, {cold_ms:.0f}ms cold")
    t0 = time.time()
    call(TUNER_PORT, "/api/unmatched-faces/people")
    report.check("queue is cached", (time.time() - t0) * 1000 < 500,
                 f"{(time.time() - t0) * 1000:.0f}ms warm")

    print("\nTagTuner: exclude a face, then restore it")
    # Pick the face out of somebody's candidate list rather than taking whichever face
    # has the highest id. The check below is that an excluded face stops being offered;
    # a face nobody was being offered in the first place passes it without testing it.
    peer, face, offered_before = None, None, set()
    for candidate in db_all(
        work_db,
        "SELECT id FROM faces WHERE name IS NULL AND excluded = 0 ORDER BY id DESC LIMIT 40",
    ):
        _, cand = call(TUNER_PORT, f"/api/face-matches-unmatched?id={candidate}")
        ids = {m["id"] for m in cand.get("matches", [])} if isinstance(cand, dict) else set()
        if ids:
            peer, offered_before, face = candidate, ids, max(ids)
            break
    if face is None:
        report.check("a face was available to exclude", False,
                     "no unnamed face is offered as a candidate to any other")
    else:
        status, body = call(TUNER_PORT, "/api/faces/exclude",
                            {"face_ids": [face], "reason": "stranger"})
        report.check("exclude accepted", status == 200, str(body)[:60])
        _, q2 = call(TUNER_PORT, "/api/unmatched-faces/people")
        report.check("Excluded bucket appears",
                     "Excluded" in {e["name"] for e in q2} if isinstance(q2, list) else False)
        _, listing = call(TUNER_PORT, "/api/faces/excluded")
        report.check("bucket shows the reason",
                     any(f["id"] == face and f["reason"] == "stranger"
                         for f in listing.get("faces", [])))
        _, offered = call(TUNER_PORT, f"/api/face-matches-unmatched?id={peer}")
        ids = {m["id"] for m in offered.get("matches", [])} if isinstance(offered, dict) else set()
        report.check(
            "excluded face is not offered to anyone",
            face not in ids,
            f"face {peer} was offered {len(offered_before)} candidate(s) including {face}, "
            f"now {len(ids)}",
        )
        call(TUNER_PORT, "/api/faces/restore", {"face_ids": [face]})
        report.check("restore leaves no residue",
                     db(work_db, "SELECT excluded FROM faces WHERE id = ?", (face,)) == 0)

    print("\nTagPup: the faces on a photo")
    sample = db(work_db, "SELECT photo_path FROM faces GROUP BY photo_path"
                         " ORDER BY COUNT(*) DESC LIMIT 1")
    if sample:
        _, faces = call(TAGPUP_PORT, f"/api/photo-faces?path={urllib.parse.quote(sample)}")
        report.check("photo-faces lists them", faces.get("total", 0) > 0,
                     f"{faces.get('total')} faces, {faces.get('unmatched')} unidentified")
        suggested = [f for f in faces.get("faces", []) if f.get("suggestion")]
        if suggested:
            sims = [f["similarity"] for f in suggested]
            report.check("every suggestion clears the 0.5 floor", min(sims) >= 0.5,
                         f"{len(suggested)} suggested, min={min(sims):.3f}")
        else:
            report.check("suggestions behave", True, "none confident enough, which is valid")
        first = faces.get("faces", [{}])[0]
        if first.get("id"):
            status, raw = call(TAGPUP_PORT, f"/api/face-crop?id={first['id']}")
            report.check("face crop is served", status == 200 and len(raw) > 0,
                         f"{len(raw) if isinstance(raw, bytes) else 0} bytes")

    if folder:
        print("\nTagPup: tag suggestions")
        q = urllib.parse.quote(folder)
        status, body = call(TAGPUP_PORT, "/api/folder/suggest-start", {"folder_path": folder})
        report.check("suggest-start accepted", status == 200, str(body)[:60])
        sug, deadline = {}, time.time() + 1800
        while time.time() < deadline:
            _, sug = call(TAGPUP_PORT, f"/api/folder/suggest-status?path={q}")
            if isinstance(sug, dict) and sug.get("status") in ("completed", "error"):
                break
            time.sleep(3)
        report.check("suggestions completed",
                     isinstance(sug, dict) and sug.get("status") == "completed",
                     f"{sug.get('completed')}/{sug.get('total')}")
        scores = []
        for entry in (sug.get("suggestions") or {}).values():
            scores += [t["score"] for t in entry.get("tags", [])]
            scores += [p["score"] for p in entry.get("people", [])]
        report.check("suggestions were produced", len(scores) > 0, f"{len(scores)} scored items")
        if scores:
            report.check("scores reach the display threshold",
                         max(scores) >= 0.6,
                         f"max={max(scores):.2f}, {sum(1 for x in scores if x >= 0.6)} at/above 0.6")


if __name__ == "__main__":
    raise SystemExit(main())
