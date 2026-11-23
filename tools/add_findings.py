"""Number findings into docs/findings.md, archive the closed ones, and set statuses.

Rows come from a file in findings.md's format, numbered `| ? |`, as workers and reviewers
report them. Numbers are given from the highest across findings.md AND the archives
(docs/history/findings_closed_*.md), so an archived number is never given again.

    .venv/Scripts/python.exe tools/add_findings.py rows.md
    .venv/Scripts/python.exe tools/add_findings.py --status 266 "fixed: 29f101a"
    .venv/Scripts/python.exe tools/add_findings.py --archive
    .venv/Scripts/python.exe tools/add_findings.py --open     # the open rows and their count, to quote in a report
    .venv/Scripts/python.exe tools/add_findings.py --watch 2360 "no repro; look here when a report sounds like it"

A status replaces a row's last column, in findings.md or in the archive holding the row.
`--archive` moves every closed row (status starting fixed, built, decided, closed,
accepted, ...) from findings.md to the archive of its number range, byte for byte.
`--watch N "why"` moves an open row, byte for byte but for its status, to docs/watching.md: a finding with nothing to do
until it is reported again or reproduced. It is not counted by `--open` and not brought up again; it is read when an issue
is reported, to see whether it is that one (owner, 2026-11-02). Its number is kept and never given again.
A row naming a sweep, backfill, `--apply` or bulk write is not closed `fixed` unless the status says `wrote N` (the count the
fix wrote, 0 included; owner, 2026-11-08).
A `data` or `wrong` row naming a module that the commits of two earlier `data`/`wrong` rows closed `fixed: <commit>` changed
is a third fix for one place, and is refused unless it says "second fix of #n: the single place is <place>; guarded by
<test>", the test that fails on a second reader or writer (#2924, #3344). Rows of a branch's file closed `fixed` count too.
A new `low` row says `live: N` (rows it touches, counted read-only) or `user action: ...`; `live: 0` closes it
`decided: no live instance, not built` in the same call (retro 2026-11-14).
Nothing is written unless every row parses and every numbered row exists.

A worker on a branch does NOT number into findings.md: two branches that both number
rows collide on merge, and the second is renumbered by hand. A worker keeps its rows in
a file of its own, with provisional numbers, which merges without a conflict:

    python tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md
    python tools/add_findings.py --branch-file docs/findings_pending/<branch>.md --status B-2 "fixed: abc"

(rows `| ? |` become `B-1`, `B-2`, ...; do not write a `B-n` into code, tests or commit
messages, since nothing renumbers them). The main session, when it merges the branch:

    python tools/add_findings.py --take docs/findings_pending/<branch>.md

gives each row its number in findings.md in order, replaces a `B-n` named in another row of
the file by the number, and removes the file. Prints the numbers given.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup.core import processes  # noqa: E402

FINDINGS = os.path.join(ROOT, "docs", "findings.md")
ARCHIVE_DIR = os.path.join(ROOT, "docs", "history")
WATCHING = os.path.join(ROOT, "docs", "watching.md")
WATCHING_TITLE = ("# Findings being watched\n\nRows moved from [findings.md](findings.md) by `tools/add_findings.py --watch`: "
                  "nothing to do until the problem is reported again or reproduced (owner, 2026-11-02). Not counted as open and "
                  "not brought up. **Read this when an issue is reported, to see whether it is one of these**; a row that is "
                  "reproduced goes back to findings.md by hand, with status `open`.\n\n" + "| # | Found | Finding | Status |\n"
                  "|---|---|---|---|\n")
CHUNK = 250
NUMBERED = re.compile(r"^\| (\d+) \|", re.M)
ARCHIVE_NAME = re.compile(r"^findings_closed_(\d{4})-(\d{4})\.md$")
#: A status that starts like one of these is closed: nothing is waiting on the row.
CLOSED = re.compile(r"(?:fixed|built|decided|decision|closed|accepted|done|obsolete|replaced|documented|"
                    r"measured|noted|counted|investigated|not reproduced|not applicable|not a defect)\b", re.I)
HEADER = "| # | Found | Finding | Status |\n|---|---|---|---|"
BRANCH_TITLE = ("# Findings of a branch\n\nProvisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in\n"
                "docs/findings.md when the main session merges the branch, and removes this file.\n\n" + HEADER + "\n")


def rows_in(text):
    """The `| ? |` rows of a report, in order."""
    return [line.rstrip() for line in text.splitlines() if line.startswith("| ? |")]


#: A new `low` row names how many live rows it touches (counted read-only) or the user action that makes it; live 0
#: is closed at birth. Seven lows a sweep or review spawned were built and reverted for 0 live (retro 2026-11-14).
LIVE_COUNT = re.compile(r"\blive[: ]+(\d+)|\buser action:", re.I)


def vet_lows(rows):
    """(the rows with a `live: 0` low closed `decided`, the names of the lows that give no count nor user action)."""
    vetted, bare = [], []
    for row in rows:
        cells = row.split("|")
        if len(cells) > 3 and re.match(r"\s*low:", cells[3], re.I):
            found = LIVE_COUNT.search(row)
            if not found:
                bare.append(row.split("|")[2].strip() or "?")
            elif found.group(1) == "0" and not is_closed(row):
                row = row.rstrip().rstrip("|").rsplit("|", 1)[0] + "| decided: no live instance, not built |"
        vetted.append(row)
    return vetted, bare


def row_status(line):
    """A row's last column."""
    return line.rstrip().rstrip("|").rsplit("|", 1)[1].strip()


def is_closed(line):
    return bool(CLOSED.match(row_status(line)))


def open_rows(text):
    """[(number, severity, status)] of the numbered rows whose status is not a closed word, in order. A `planned:`
    or `tabled` row is open: something is still waiting on it (retro 2026-10-18)."""
    found = []
    for line in text.splitlines():
        match = re.match(r"^\| (\d+) \|", line)
        if match and not is_closed(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            found.append((int(match.group(1)), cells[2].split(":", 1)[0][:12], row_status(line)[:60]))
    return found


def watch(findings, watching, key, reason):
    """(findings without the row `key`, watching with it, its status set to `watching: reason`), or None if findings has no
    such row."""
    match = re.search(r"^\| %s \|[^\n]*\n?" % re.escape(str(key)), findings, re.M)
    if not match:
        return None
    row = set_status(match.group(0).rstrip("\n"), key, "watching: " + reason.strip())
    kept = findings[:match.start()] + findings[match.end():]
    return kept, insert_rows(watching or WATCHING_TITLE, [row])


def without_first_cell(row):
    """The row from its second cell on, starting with the `|` that ends the first."""
    return row[row.index("|", 1):]


#: A row naming one of these is about a write over many files or rows; closing it `fixed` must say what the fix wrote.
BULK = re.compile(r"sweep|backfill|--apply|bulk write", re.I)
WROTE = re.compile(r"\bwrote \d+")


def unproven_bulk(text, key, status):
    """True when row `key` of `text` names a bulk write and `status` closes it `fixed` without `wrote N` (a sweep read
    60 photos, reported 60 done and wrote nothing, #2622)."""
    match = re.search(r"^\| %s \|[^\n]*$" % re.escape(str(key)), text, re.M)
    return bool(match and BULK.search(match.group(0)) and re.match(r"fixed\b", status.strip(), re.I)
                and not WROTE.search(status))


COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
SEVERE = re.compile(r"^\|[^|]*\|[^|]*\|\s*(data|wrong):", re.I)


def unproven_commit(text, key, status):
    """True when row `key` of `text` is `data:` or `wrong:` and `status` closes it `fixed` naming no commit hash, so
    the chain check cannot read the fix's files (#2963)."""
    match = re.search(r"^\| %s \|[^\n]*$" % re.escape(str(key)), text, re.M)
    return bool(match and SEVERE.match(match.group(0)) and re.match(r"fixed\b", status.strip(), re.I)
                and not COMMIT.search(status))


def set_status(text, key, status):
    """`text` with the row `key` (a number or `B-n`) given `status`, or None if it has no such row."""
    match = re.search(r"^\| %s \|[^\n]*$" % re.escape(str(key)), text, re.M)
    if not match:
        return None
    body = match.group(0).rstrip().rstrip("|").rsplit("|", 1)[0]
    return text.replace(match.group(0), body + "| " + status.strip() + " |", 1)


def insert_rows(text, numbered):
    """`text` with the lines `numbered` after its last numbered row (after the table's header if none)."""
    last = None
    for match in NUMBERED.finditer(text):
        last = match
    if last is not None:
        end = text.index("\n", last.start()) if "\n" in text[last.start():] else len(text)
    else:
        sep = re.search(r"^\|[-| :]+\|$", text, re.M)
        if not sep:
            raise ValueError("findings.md has no table to add to")
        end = sep.end()
    return text[:end] + "\n" + "\n".join(numbered) + text[end:]


def highest(*texts):
    numbers = [int(n) for text in texts for n in NUMBERED.findall(text)]
    return max(numbers) if numbers else 0


def add(findings, rows, statuses=(), floor=0):
    """`findings` with `rows` numbered after the highest number (and after `floor`, the highest
    in the archives) and `statuses` ((number, text)) set. Returns (the new text, the numbers given)."""
    for number, status in statuses:
        updated = set_status(findings, number, status)
        if updated is None:
            raise ValueError("no finding #%d" % number)
        findings = updated
    if not rows:
        return findings, []
    last = max(highest(findings), floor)
    given = list(range(last + 1, last + 1 + len(rows)))
    numbered = ["| %d |%s" % (n, without_first_cell(row)[1:]) for n, row in zip(given, rows)]
    return insert_rows(findings, numbered), given


def archive_name(number):
    low = ((number - 1) // CHUNK) * CHUNK + 1
    return "findings_closed_%04d-%04d.md" % (low, low + CHUNK - 1)


def archive_text(name, rows):
    low, high = ARCHIVE_NAME.match(name).groups()
    return ("# Closed findings %d-%d\n\nRows moved byte for byte from [findings.md](../findings.md) when their status closed "
            "(fixed, built, decided, accepted, ...); their numbers never change. Open rows are in findings.md.\n\n%s\n%s\n"
            % (int(low), int(high), HEADER, "\n".join(rows)))


def archive(findings, archives):
    """Move the closed rows of `findings` into `archives` ({file name: text}). Returns (new findings,
    the archives that changed, how many rows moved). Idempotent."""
    keep, moved = [], {}
    for line in findings.split("\n"):
        match = re.match(r"\| (\d+) \|", line)
        if match and is_closed(line):
            moved.setdefault(archive_name(int(match.group(1))), []).append(line)
        else:
            keep.append(line)
    changed = {}
    for name, rows in moved.items():
        have = [line for line in archives.get(name, "").split("\n") if re.match(r"\| \d+ \|", line)]
        by_number = {int(re.match(r"\| (\d+) \|", line).group(1)): line for line in have + rows}
        changed[name] = archive_text(name, [by_number[n] for n in sorted(by_number)])
    return "\n".join(keep), changed, sum(len(rows) for rows in moved.values())


def load_archives(folder):
    found = {}
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if ARCHIVE_NAME.match(name):
                with open(os.path.join(folder, name), encoding="utf-8", newline="") as handle:
                    found[name] = handle.read().replace("\r\n", "\n")
    return found


def add_branch(text, rows):
    """A branch's file with `rows` given the next provisional numbers. Returns (text, ['B-1', ...])."""
    text = text or BRANCH_TITLE
    last = max([int(n) for n in re.findall(r"^\| B-(\d+) \|", text, re.M)] or [0])
    given = ["B-%d" % n for n in range(last + 1, last + 1 + len(rows))]
    lines = ["| %s |%s" % (name, without_first_cell(row)[1:]) for name, row in zip(given, rows)]
    return text.rstrip("\n") + "\n" + "\n".join(lines) + "\n", given


def take(findings, branch, floor=0):
    """The rows of a branch's file numbered into `findings`. Returns (new text, {B-n: number})."""
    rows = [line.rstrip() for line in branch.splitlines() if re.match(r"\| (?:B-\d+|\?) \|", line)]
    if not rows:
        raise ValueError("the branch file holds no rows")
    names = [re.match(r"\| (B-\d+|\?) \|", row).group(1) for row in rows]
    last = max(highest(findings), floor)
    given = list(range(last + 1, last + 1 + len(rows)))
    mapping = {name: number for name, number in zip(names, given) if name != "?"}

    def numbered(row):
        body = re.sub(r"\bB-(\d+)\b", lambda m: "#%d" % mapping["B-" + m.group(1)] if "B-" + m.group(1) in mapping
                      else m.group(0), without_first_cell(row)[1:])
        return body

    lines = ["| %d |%s" % (n, numbered(row)) for n, row in zip(given, rows)]
    return insert_rows(findings, lines), mapping


SIMILAR_SHARED = 3     # identifier-like words two rows share before one is said to look like the other
SIMILAR_WINDOW = 150   # only rows this close in number are compared


def identifiers(row):
    """The identifier-like words of a row (`retire-photo-vectors`, `embed_photos`, `mod.NAME`), lower case with `-` read
    as `_`, dates left out, a dotted name also by its parts. Two rows about one case share several (#1847, #1850)."""
    found = set()
    for word in re.findall(r"[A-Za-z0-9_.\-/]+", row):
        word = word.strip(".-/").lower().replace("-", "_")
        if len(word) < 8 or re.match(r"\d{4}_\d\d_\d\d$", word) or not re.search(r"[_./]", word):
            continue
        found.add(word)
        found.update(part for part in re.split(r"[./]", word) if len(part) >= 8 and "_" in part)
    return found


def similar_rows(new_rows, existing):
    """[(new row's number or name, the existing number it looks like)] for each new row that shares SIMILAR_SHARED
    identifiers with a row of `existing` ({number: row text}) within SIMILAR_WINDOW numbers and does not cite it as
    `#n`. A notice, not a refusal: related rows are common (29 of 100 rows shared four with an older one, 2026-10-24),
    so a refusal would be wrong too often; the main session cites the row, or writes the new one into it."""
    newest = max(existing, default=0)
    seen = []
    for row in new_rows:
        name = row.split("|")[1].strip()
        words = identifiers(without_first_cell(row))
        for number, text in sorted(existing.items()):
            if newest - number <= SIMILAR_WINDOW and "#%d" % number not in row \
                    and len(words & identifiers(without_first_cell(text))) >= SIMILAR_SHARED:
                seen.append((name, number))
    return seen


#: A row closed `fixed` names the commit of its fix; a module the fixes of two `data`/`wrong` rows changed, named by a third,
#: is one place fixed a third time (the clip that would not save: #2893, #2908, #2918; retro 2026-11-13, #2924).
COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b")
CODE_FILE = re.compile(r"^(?:tagpup|web|scripts|tools|\.claude/hooks)/")
SINGLE_PLACE = re.compile(r"second fix of #?(\d+|B-\d+): the single place is \S", re.I)
#: The escape also names the test that fails on a second reader or writer of that place (#3344).
GUARDED = re.compile(r"guarded by `?[\w./:-]*test[\w./:-]*", re.I)


def severe(row):
    """True for a `data:` or `wrong:` row: data lost or a wrong result the owner sees, not a `low`."""
    cells = row.split("|")
    return len(cells) > 3 and re.match(r"\s*(?:data|wrong):", cells[3], re.I) is not None


def fix_commits(row):
    """The commits a row closed `fixed` names."""
    status = row_status(row)
    return COMMIT.findall(status) if re.match(r"fixed\b", status, re.I) else []


def names_file(text, path):
    """True when a code span of `text` names the module at `path`: a stem holding `_` as a word (`clip_patch.packet_of`), a
    one-word stem only with its package or its extension (`store.photos`, `images.py`): `photos` is also a table and
    `selection` a word."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if "_" in stem:
        pattern = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(stem)
    else:
        parent = re.escape(os.path.basename(os.path.dirname(path)))
        pattern = r"(?<![A-Za-z0-9_])(?:%s[./]%s(?![A-Za-z0-9_])|%s\.(?:py|js|mjs)\b)" % (parent, re.escape(stem),
                                                                                       re.escape(stem))
    return any(re.search(pattern, span) for span in re.findall(r"`([^`]+)`", text))


def third_fixes(new_rows, existing, files_of, branch_rows=()):
    """[(new row's number or name, the module's path, the earlier numbers)] for each `data`/`wrong` new row naming a module
    (`names_file`) that the commits of two or more `data`/`wrong` rows of `existing` closed `fixed` changed (two rows and
    two commits, within SIMILAR_WINDOW numbers), unless the row says "second fix of #n: the single place is ..." of one of
    them AND "guarded by <test>" (the test that fails on a second reader or writer), or a row of `existing` naming the
    module was closed `decided` saying where its single place is. `branch_rows` are the rows of a branch's file
    (`| B-n | ...`), counted as fixes before they are numbered (#3344). `files_of(commit)`
    is the paths a commit changed (none for a merge or a name that is no commit). Measured on rows #2700-#2950 as each was
    numbered: #2918 refused, and 2 more (#2841, #2843, the Recycle Bin's third and fourth fixes after #2700 and #2710)."""
    if not any(severe(row) for row in new_rows):
        return []
    newest = max(existing, default=0)
    fixes, settled = {}, []
    kept = list(existing.items()) + [(re.match(r"\| (B-\d+) \|", row).group(1), row) for row in branch_rows
                                      if re.match(r"\| B-\d+ \|", row)]
    for number, text in kept:
        if re.match(r"decided\b", row_status(text), re.I) and "single place" in text:
            settled.append(without_first_cell(text))
        if (isinstance(number, int) and newest - number > SIMILAR_WINDOW) or not severe(text):
            continue
        for commit in fix_commits(text):
            for path in files_of(commit):
                if CODE_FILE.match(path):
                    fixes.setdefault(path, set()).add((number, commit))
    found = []
    for row in new_rows:
        if not severe(row):
            continue
        body, escape = without_first_cell(row), SINGLE_PLACE.search(row)
        for path, made in sorted(fixes.items()):
            numbers = sorted({number for number, _commit in made}, key=str)
            if len(numbers) < 2 or len({commit for _number, commit in made}) < 2 or not names_file(body, path):
                continue
            said = escape.group(1) if escape else ""
            if (escape and GUARDED.search(row) and (int(said) if said.isdigit() else said) in numbers) \
                    or any(names_file(text, path) for text in settled):
                continue
            found.append((row.split("|")[1].strip(), path, numbers))
    return found


def git_files(repo):
    """files_of for `third_fixes`: the paths each commit changed in `repo`, asked of git once per commit."""
    seen = {}

    def files_of(commit):
        if commit not in seen:
            done = processes.run(["git", "-C", repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace")
            seen[commit] = done.stdout.splitlines() if done.returncode == 0 else []
        return seen[commit]
    return files_of


def existing_rows(*texts):
    return {int(m.group(1)): line for text in texts for line in text.splitlines()
            if (m := re.match(r"\| (\d+) \|", line))}


def _read(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _watching_text(path):
    return _read(path).replace("\r\n", "\n") if os.path.exists(path) else ""


def _write(path, text, newline="\n"):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", newline))


def _check_rows(parser, rows, findings, archive_dir, watching, repo, branch_rows=()):
    """Refuse a third fix for one place (`third_fixes`), before anything is written; then the notice of a row that looks
    like an earlier one."""
    existing = existing_rows(findings.replace("\r\n", "\n"), watching.replace("\r\n", "\n"),
                             *load_archives(archive_dir).values())
    refused = third_fixes(rows, existing, git_files(repo), branch_rows)
    if refused:
        parser.error("\n".join(
            "row %s names %s, which the fixes of %s changed: a third fix for one place. Ask which single place should own "
            "it, and write 'second fix of #n: the single place is <place>; guarded by <the test that fails on a second reader "
            "or writer>' in the row" % (name, path, ", ".join(str(n) if str(n).startswith("B-") else "#%d" % n
                                                               for n in numbers))
            for name, path, numbers in refused))
    for name, number in similar_rows(rows, existing):
        print("notice: row %s looks like #%d (shares %d or more identifiers); cite it as #%d or fold the row into it"
              % (name, number, SIMILAR_SHARED, number))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rows", nargs="?", help="a file holding `| ? |` rows")
    parser.add_argument("--status", nargs=2, action="append", default=[], metavar=("NUMBER", "TEXT"),
                        help="set a row's status (its last column)")
    parser.add_argument("--archive", action="store_true", help="move the closed rows to docs/history/")
    parser.add_argument("--open", action="store_true",
                        help="print the open rows and their count: the number a merge or phase-end report quotes")
    parser.add_argument("--watch", nargs=2, action="append", default=[], metavar=("NUMBER", "WHY"),
                        help="move an open row to docs/watching.md: nothing to do until it is reported again")
    parser.add_argument("--watching-file", default=WATCHING, help=argparse.SUPPRESS)
    parser.add_argument("--branch-file", metavar="PATH", help="keep the rows (and statuses) in this file with B-n numbers")
    parser.add_argument("--take", metavar="PATH", help="number a branch's file into findings.md and remove it")
    parser.add_argument("--findings", default=FINDINGS, help=argparse.SUPPRESS)
    parser.add_argument("--archive-dir", default=ARCHIVE_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--repo", default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    rows = []
    if args.rows:
        rows = rows_in(_read(args.rows))
        if not rows:
            parser.error("no `| ? |` row in %s" % args.rows)
        rows, bare = vet_lows(rows)
        if bare:
            parser.error("a new `low` row says how many live rows it touches (`live: N`, counted read-only) or the user action "
                         "that makes it (`user action: ...`); with `live: 0` it is closed `decided` and not built "
                         "(CLAUDE.md, 'a special case needs a real instance'): row(s) dated %s" % ", ".join(bare))

    if args.open:
        found = open_rows(_read(args.findings).replace("\r\n", "\n"))
        for number, severity, status in found:
            print("#%d %s: %s" % (number, severity, status))
        print("%d open" % len(found))
        return 0

    if args.branch_file:
        text = _read(args.branch_file).replace("\r\n", "\n") if os.path.exists(args.branch_file) else ""
        given = []
        if rows:
            _check_rows(parser, rows, _read(args.findings), args.archive_dir, _watching_text(args.watching_file), args.repo,
                        text.splitlines())
            text, given = add_branch(text, rows)
        for key, status in args.status:
            if unproven_commit(text, key, status):
                parser.error("%s is data or wrong: close it `fixed: <commit hash> ...` so the chain check can read its files" % key)
            if unproven_bulk(text, key, status):
                parser.error("%s names a bulk write: close it `fixed: <commit>; wrote N ...` with what the fix wrote" % key)
            updated = set_status(text, key, status)
            if updated is None:
                parser.error("no row %s in %s" % (key, args.branch_file))
            text = updated
        _write(args.branch_file, text)
        if given:
            print("provisional %s-%s" % (given[0], given[-1]))
        return 0

    text = _read(args.findings)
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    archives = load_archives(args.archive_dir)
    watching = _watching_text(args.watching_file)
    floor = highest(*archives.values(), watching)
    given, mapping, changed, moved = [], {}, {}, 0
    for key, status in args.status:
        if unproven_commit(text, key, status) or unproven_commit("\n".join(archives.values()), key, status):
            parser.error("#%s is data or wrong: close it `fixed: <commit hash> ...` so the chain check can read its files" % key)
        if unproven_bulk(text, key, status) or unproven_bulk("\n".join(archives.values()), key, status):
            parser.error("#%s names a bulk write: close it `fixed: <commit>; wrote N ...` with what the fix wrote" % key)
        if set_status(text, key, status) is not None:
            text = set_status(text, key, status)
            continue
        name = archive_name(int(key)) if key.isdigit() else None
        if name in archives and set_status(archives[name], key, status) is not None:
            if not CLOSED.match(status.strip()):
                parser.error("#%s is archived: a reopened row is moved back to findings.md by hand" % key)
            archives[name] = set_status(archives[name], key, status)
            changed[name] = archives[name]
            continue
        parser.error("no finding #%s" % key)
    watched = []
    for key, why in args.watch:
        moved_row = watch(text, watching, key, why)
        if moved_row is None:
            parser.error("no open finding #%s" % key)
        text, watching = moved_row
        watched.append(key)
    if rows:
        _check_rows(parser, rows, text, args.archive_dir, watching, args.repo)
        text, given = add(text, rows, floor=floor)
    if args.take:
        taken = _read(args.take).replace("\r\n", "\n")
        _check_rows(parser, [line for line in taken.splitlines() if re.match(r"\| (?:B-\d+|\?) \|", line)], text,
                    args.archive_dir, watching, args.repo)
        text, mapping = take(text, _read(args.take).replace("\r\n", "\n"), floor=floor)
    if args.archive:
        text, moves, moved = archive(text, archives)
        changed.update(moves)
    _write(args.findings, text, newline)
    if watched:
        _write(args.watching_file, watching, newline)
    for name, body in changed.items():
        _write(os.path.join(args.archive_dir, name), body)
    if args.take:
        os.remove(args.take)
        print("numbered %s" % ", ".join("%s -> %d" % pair for pair in mapping.items()))
    if given:
        print("numbered %d-%d" % (given[0], given[-1]))
    for number, _status in args.status:
        print("status of #%s set" % number)
    for number in watched:
        print("#%s moved to docs/watching.md" % number)
    if args.archive:
        print("archived %d closed row(s)" % moved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
