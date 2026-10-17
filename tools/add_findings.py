"""Number findings into docs/findings.md, archive the closed ones, and set statuses.

Rows come from a file in findings.md's format, numbered `| ? |`, as workers and reviewers
report them. Numbers are given from the highest across findings.md AND the archives
(docs/history/findings_closed_*.md), so an archived number is never given again.

    .venv/Scripts/python.exe tools/add_findings.py rows.md
    .venv/Scripts/python.exe tools/add_findings.py --status 266 "fixed: 29f101a"
    .venv/Scripts/python.exe tools/add_findings.py --archive

A status replaces a row's last column, in findings.md or in the archive holding the row.
`--archive` moves every closed row (status starting fixed, built, decided, closed,
accepted, ...) from findings.md to the archive of its number range, byte for byte.
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
FINDINGS = os.path.join(ROOT, "docs", "findings.md")
ARCHIVE_DIR = os.path.join(ROOT, "docs", "history")
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


def row_status(line):
    """A row's last column."""
    return line.rstrip().rstrip("|").rsplit("|", 1)[1].strip()


def is_closed(line):
    return bool(CLOSED.match(row_status(line)))


def without_first_cell(row):
    """The row from its second cell on, starting with the `|` that ends the first."""
    return row[row.index("|", 1):]


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


def _read(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path, text, newline="\n"):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", newline))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rows", nargs="?", help="a file holding `| ? |` rows")
    parser.add_argument("--status", nargs=2, action="append", default=[], metavar=("NUMBER", "TEXT"),
                        help="set a row's status (its last column)")
    parser.add_argument("--archive", action="store_true", help="move the closed rows to docs/history/")
    parser.add_argument("--branch-file", metavar="PATH", help="keep the rows (and statuses) in this file with B-n numbers")
    parser.add_argument("--take", metavar="PATH", help="number a branch's file into findings.md and remove it")
    parser.add_argument("--findings", default=FINDINGS, help=argparse.SUPPRESS)
    parser.add_argument("--archive-dir", default=ARCHIVE_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    rows = []
    if args.rows:
        rows = rows_in(_read(args.rows))
        if not rows:
            parser.error("no `| ? |` row in %s" % args.rows)

    if args.branch_file:
        text = _read(args.branch_file).replace("\r\n", "\n") if os.path.exists(args.branch_file) else ""
        given = []
        if rows:
            text, given = add_branch(text, rows)
        for key, status in args.status:
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
    floor = highest(*archives.values())
    given, mapping, changed, moved = [], {}, {}, 0
    for key, status in args.status:
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
    if rows:
        text, given = add(text, rows, floor=floor)
    if args.take:
        text, mapping = take(text, _read(args.take).replace("\r\n", "\n"), floor=floor)
    if args.archive:
        text, moves, moved = archive(text, archives)
        changed.update(moves)
    _write(args.findings, text, newline)
    for name, body in changed.items():
        _write(os.path.join(args.archive_dir, name), body)
    if args.take:
        os.remove(args.take)
        print("numbered %s" % ", ".join("%s -> %d" % pair for pair in mapping.items()))
    if given:
        print("numbered %d-%d" % (given[0], given[-1]))
    for number, _status in args.status:
        print("status of #%s set" % number)
    if args.archive:
        print("archived %d closed row(s)" % moved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
