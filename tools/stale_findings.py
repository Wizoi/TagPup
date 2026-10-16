"""List the open rows of docs/findings.md that name a file or a symbol the code no longer has.

A stale open row makes a worker chase something that is gone (owner, 2026-10-15). Each backticked
name in an open row's Finding is looked for: a path (`tagpup/services/sync.py:457`, `web/tagpup/cache.js`)
must exist, from the repository or from tagpup/, tests/ or web/; a code name (`store.photos.record_tags`,
`_under_one_of`, `owner()`) must appear, by its last part, as a word in some code file. A row naming
one that is gone is printed with what is missing: re-check it against the trunk and close or keep it
(tools/add_findings.py --status). A name that is still there does not prove the row open.

    .venv/Scripts/python.exe tools/stale_findings.py

Exits 1 when a row is listed, else 0.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import add_findings  # noqa: E402

CODE_DIRS = ("tagpup", "web", "tools", "scripts", "tests")
CODE_TYPES = (".py", ".js", ".mjs", ".cjs", ".html", ".css")
TICKED = re.compile(r"`([^`\n]+)`")
PATH = re.compile(r"^[\w.\-/\\]+\.(?:py|js|mjs|cjs|html|css|md|json|ps1)$")
LINE_SUFFIX = re.compile(r"\s*~?:[\d\-, ~:]+$")
NAME = re.compile(r"^[A-Za-z_][\w.]*(?:\(\))?$")
#: Path prefixes a row may leave out.
UNDER = ("", "tagpup", "tests", "web")


def open_rows(text):
    """[(number, the Finding cell)] of the rows whose status is not closed."""
    found = []
    for line in text.splitlines():
        match = re.match(r"\| (\d+) \|", line)
        if match and not add_findings.is_closed(line):
            cells = line.rstrip().rstrip("|").split(" | ")
            found.append((int(match.group(1)), " | ".join(cells[2:-1])))
    return found


def code_words(root):
    """Every identifier-like word of the code files under `root`."""
    words = set()
    names = [os.path.join(root, n) for n in os.listdir(root) if n.endswith(".py")]
    for top in CODE_DIRS:
        for folder, subs, files in os.walk(os.path.join(root, top)):
            subs[:] = [s for s in subs if s not in ("node_modules", "__pycache__")]
            names += [os.path.join(folder, f) for f in files if f.endswith(CODE_TYPES)]
    for name in names:
        with open(name, encoding="utf-8", errors="replace") as handle:
            words.update(re.findall(r"[A-Za-z_]\w*", handle.read()))
    return words


def missing(finding, root, words):
    """The backticked names of `finding` that the code under `root` no longer has."""
    gone = []
    for ticked in TICKED.findall(finding):
        name = LINE_SUFFIX.sub("", ticked.strip())
        if PATH.match(name) and ("/" in name or "\\" in name):
            if not any(os.path.exists(os.path.join(root, under, name)) for under in UNDER):
                gone.append(ticked)
        elif NAME.match(name):
            last = name.rstrip("()").rsplit(".", 1)[-1]
            # A plain word (`photos`, `held`) says nothing; only a name with "_" or a dotted one is looked for.
            if ("_" in last.strip("_") or "." in name) and len(last) > 3 and last not in words:
                gone.append(ticked)
    return gone


def stale(text, root):
    """[(number, [missing names])] of the open rows of findings `text` that name what `root` no longer has."""
    words = code_words(root)
    listed = []
    for number, finding in open_rows(text):
        gone = missing(finding, root, words)
        if gone:
            listed.append((number, gone))
    return listed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--findings", default=add_findings.FINDINGS, help=argparse.SUPPRESS)
    parser.add_argument("--root", default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    with open(args.findings, encoding="utf-8") as handle:
        listed = stale(handle.read(), args.root)
    for number, gone in listed:
        print("#%d names what the code no longer has: %s" % (number, ", ".join(gone)))
    print("%d open row(s) to re-check" % len(listed))
    return 1 if listed else 0


if __name__ == "__main__":
    sys.exit(main())
