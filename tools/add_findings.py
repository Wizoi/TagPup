"""Number findings into docs/findings.md, and set the status of rows already there.

Rows come from a file in findings.md's format, numbered `| ? |`, as workers and reviewers
report them; each is given the next number, in order, after the last row of the table.

    .venv/Scripts/python.exe tools/add_findings.py rows.md
    .venv/Scripts/python.exe tools/add_findings.py rows.md --status 266 "fixed: 29f101a"

A status replaces a row's last column. Nothing is written unless every row parses and
every numbered row exists. Prints the numbers given.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINDINGS = os.path.join(ROOT, "docs", "findings.md")
NUMBERED = re.compile(r"^\| (\d+) \|", re.M)


def rows_in(text):
    """The `| ? |` rows of a report, in order."""
    return [line.rstrip() for line in text.splitlines() if line.startswith("| ? |")]


def add(findings, rows, statuses=()):
    """`findings` with `rows` numbered after its last row and `statuses` ((number, text))
    set. Returns (the new text, the numbers given)."""
    for number, status in statuses:
        match = re.search(r"^\| %d \|[^\n]*$" % number, findings, re.M)
        if not match:
            raise ValueError("no finding #%d" % number)
        body = match.group(0).rstrip().rstrip("|").rsplit("|", 1)[0]
        findings = findings.replace(match.group(0), body + "| " + status.strip() + " |")
    if not rows:
        return findings, []
    numbers = [int(n) for n in NUMBERED.findall(findings)]
    if not numbers:
        raise ValueError("findings.md has no numbered row to follow")
    last = max(numbers)
    anchor = re.search(r"^\| %d \|[^\n]*$" % last, findings, re.M).group(0)
    given = list(range(last + 1, last + 1 + len(rows)))
    numbered = ["| %d |%s" % (n, row[len("| ? |"):]) for n, row in zip(given, rows)]
    return findings.replace(anchor, anchor + "\n" + "\n".join(numbered), 1), given


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rows", nargs="?", help="a file holding `| ? |` rows")
    parser.add_argument("--status", nargs=2, action="append", default=[], metavar=("NUMBER", "TEXT"),
                        help="set a row's status (its last column)")
    parser.add_argument("--findings", default=FINDINGS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    rows = []
    if args.rows:
        with open(args.rows, encoding="utf-8") as handle:
            rows = rows_in(handle.read())
    with open(args.findings, encoding="utf-8", newline="") as handle:
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    text, given = add(text.replace("\r\n", "\n"), rows, [(int(n), s) for n, s in args.status])
    with open(args.findings, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", newline))
    if given:
        print("numbered %d-%d" % (given[0], given[-1]))
    for number, _status in args.status:
        print("status of #%s set" % number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
