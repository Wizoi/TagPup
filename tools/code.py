"""Read code by part, not by file: a slim reader for the shell (retro 2026-11-13: a worker's context was mostly reading).

    .venv/Scripts/python.exe tools/code.py outline tagpup/store/journal.py
    .venv/Scripts/python.exe tools/code.py show tagpup/store/journal.py::record
    .venv/Scripts/python.exe tools/code.py show tagpup/store/journal.py::Journal.apply
    .venv/Scripts/python.exe tools/code.py show docs/ARCHITECTURE.md::"The processes"
    .venv/Scripts/python.exe tools/code.py refs readonly_uri --max 20

outline  the definitions of a file with their line spans: Python by its syntax tree (functions, classes and their
         methods, module constants), JavaScript (.js, .mjs, .cjs) by its function, class, method, exported and arrow
         lines, markdown by its headings.
show     one definition (a function, a class, a constant) or a markdown section, with line numbers. A name defined
         twice is ambiguous: the candidates are listed, to be named as `Class.name` or `name@LINE`. A name that is not
         there lists the nearest names.
refs     the lines naming a word, whole-word, under tagpup/, tests/, web/, scripts/ and tools/, as path:line:text,
         at most --max (default 40), saying how many more there are.

Every output is capped and says where it cut. It reads only: nothing is written and no process is started. A file that
is not UTF-8 is read with its bad bytes replaced; CRLF reads as LF. A path may use either slash, and a relative path is
found from the working folder, then from the repository. Exit 0 on an answer, 1 when the name is missing or ambiguous,
2 on a usage error or a file that cannot be read or parsed.
"""
import argparse
import ast
import difflib
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF_DIRS = ("tagpup", "tests", "web", "scripts", "tools")
REF_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".html")
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv"}
JS_SUFFIXES = (".js", ".mjs", ".cjs")
MD_SUFFIXES = (".md", ".markdown")

#: The caps: a cut says so, and how to read the rest.
MAX_LINES = 300
MAX_CHARS = 24000
LINE_WIDTH = 240
REFS_DEFAULT = 40
REFS_MOST = 200
#: A file over this is not read for refs (none of the code is near it; a data file is no reference).
REF_FILE_MOST = 2 * 1024 * 1024


class Failure(Exception):
    """A clear answer that is not the one asked for: `code` is the exit status."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class Output:
    """Lines written up to MAX_LINES and MAX_CHARS; past either, one line saying what was cut."""

    def __init__(self, stream, most_lines=MAX_LINES):
        self.stream = stream
        self.most = most_lines
        self.lines = 0
        self.chars = 0
        self.cut = 0

    def write(self, text):
        if len(text) > LINE_WIDTH:
            text = text[:LINE_WIDTH] + " [...]"
        if self.cut or self.lines >= self.most or self.chars + len(text) > MAX_CHARS:
            self.cut += 1
            return False
        self.stream.write(text + "\n")
        self.lines += 1
        self.chars += len(text) + 1
        return True

    def close(self, hint):
        if self.cut:
            self.stream.write("[cut: %d more line%s not shown (output cap %d lines, %d chars). %s]\n"
                              % (self.cut, "" if self.cut == 1 else "s", self.most, MAX_CHARS, hint))


def resolve(path):
    """The file a path names: either slash, from the working folder first, then from the repository."""
    path = path.strip().strip('"').replace("\\", os.sep).replace("/", os.sep)
    candidates = [path] if os.path.isabs(path) else [os.path.abspath(path), os.path.join(ROOT, path)]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise Failure("no file %s (looked from %s and from %s)" % (path, os.getcwd(), ROOT), 2)


def shown(path):
    """A path as the report names it: from the repository, forward slashes, when it is inside it."""
    relative = os.path.relpath(path, ROOT) if os.path.splitdrive(path)[0].lower() == os.path.splitdrive(ROOT)[0].lower() else path
    return (path if relative.startswith("..") else relative).replace(os.sep, "/")


def read_lines(path):
    """The file's text and lines: UTF-8 (a BOM dropped), a bad byte replaced, CRLF and CR read as LF."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as error:
        raise Failure("cannot read %s: %s" % (shown(path), error), 2) from None
    text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    return text, lines[:-1] if text.endswith("\n") else lines


class Definition:
    """One named thing of a file: its kind, its qualified name, its first and last line (1-based), its depth."""

    def __init__(self, kind, qualified, start, end, depth=0, detail=""):
        self.kind, self.qualified, self.start, self.end, self.depth, self.detail = kind, qualified, start, end, depth, detail

    @property
    def name(self):
        return self.qualified.rsplit(".", 1)[-1]

    def label(self):
        return "%d-%d %s%s %s%s" % (self.start, self.end, "  " * self.depth, self.kind, self.qualified, self.detail)


# --- Python -----------------------------------------------------------------------------------------------------------

CONSTANT = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
BLOCKS = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)


def _arguments(node):
    args = node.args
    names = [a.arg for a in args.posonlyargs + args.args]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        names.append("*")
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    text = "(" + ", ".join(names) + ")"
    return text if len(text) <= 90 else text[:86] + " ...)"


def python_definitions(path, text):
    """Every function and class at any depth, and every name a module or class body assigns, with its span."""
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as error:
        raise Failure("cannot parse %s as Python (line %s: %s); read it with Read and offset+limit"
                      % (shown(path), error.lineno, error.msg), 2) from None
    found = []

    def walk(body, prefix, depth, in_function):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                is_class = isinstance(node, ast.ClassDef)
                kind = "class" if is_class else ("async def" if isinstance(node, ast.AsyncFunctionDef) else "def")
                found.append(Definition(kind, prefix + node.name, start, node.end_lineno, depth,
                                        "" if is_class else _arguments(node)))
                walk(node.body, prefix + node.name + ".", depth + 1, not is_class)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not in_function:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        found.append(Definition("const", prefix + target.id, node.lineno, node.end_lineno, depth))
            elif isinstance(node, BLOCKS):
                # a definition under `if TYPE_CHECKING:`, a `try: import`, a loop: still its module's or function's
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(node, field, []), prefix, depth, in_function)
                for handler in getattr(node, "handlers", []):
                    walk(handler.body, prefix, depth, in_function)
    walk(tree.body, "", 0, False)
    return found


def python_outline(found):
    """The outline keeps functions, classes and the constants (UPPER_CASE) a module or class names."""
    return [d for d in found if d.kind != "const" or CONSTANT.match(d.name)]


# --- JavaScript ---------------------------------------------------------------------------------------------------------

IDENT = r"[A-Za-z_$][\w$]*"
JS_FUNCTION = re.compile(r"^(\s*)(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(" + IDENT + r")\s*\(")
JS_CLASS = re.compile(r"^(\s*)(?:export\s+(?:default\s+)?)?class\s+(" + IDENT + r")")
JS_ARROW = re.compile(r"^(\s*)(?:export\s+)?(?:const|let|var)\s+(" + IDENT + r")\s*=\s*(?:async\s+)?"
                      r"(?:function\b|\(|" + IDENT + r"\s*=>)")
JS_CONST = re.compile(r"^()(?:export\s+)?(?:const|let|var)\s+(" + IDENT + r")\s*=")
JS_METHOD = re.compile(r"^(\s+)(?:static\s+)?(?:async\s+)?(?:get\s+|set\s+)?\*?(#?" + IDENT + r")\s*\([^;]*\)\s*\{\s*$")
NOT_METHODS = {"if", "for", "while", "switch", "catch", "function", "return", "with", "else"}


#: After one of these (or at a line's start) a `/` opens a regular expression, not a division.
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")


def _regex_end(line, i):
    """The index just past the regular expression literal opening at line[i] (a `/`), or len(line)."""
    in_class = False
    i += 1
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            i += 1
        elif ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            return i + 1
        i += 1
    return len(line)


def js_end(lines, start):
    """The last line (1-based) of the statement starting at line `start`: braces, parentheses and brackets counted;
    strings, template literals, regular expressions and comments skipped. It ends at a `;` outside every bracket, or
    at the end of a line that closes the last bracket once a brace has opened outside them (a body, an object). A
    statement that never closes runs to the end of the file."""
    depth = 0
    braced = False
    quote = None
    block_comment = False
    for number in range(start, len(lines) + 1):
        line = lines[number - 1]
        before = ""   # the last significant character, to tell a regular expression from a division
        i = 0
        while i < len(line):
            ch = line[i]
            pair = line[i:i + 2]
            if block_comment:
                if pair == "*/":
                    block_comment = False
                    i += 1
            elif quote:
                if ch == "\\":
                    i += 1
                elif ch == quote:
                    quote = None
                    before = ch
            elif pair == "//":
                break
            elif pair == "/*":
                block_comment = True
                i += 1
            elif ch == "/" and (not before or before in REGEX_AFTER):
                i = _regex_end(line, i)
                before = "/"
                continue
            elif ch in "'\"`":
                quote = ch
            elif ch in "{([":
                braced = braced or (ch == "{" and depth == 0)   # a body, not a default `{}` in the parameters
                depth += 1
            elif ch in "})]":
                depth -= 1
            elif ch == ";" and depth <= 0:
                return number
            if not ch.isspace():
                before = ch
            i += 1
        if quote in ("'", '"'):
            quote = None   # a quote never spans a line
        if braced and depth <= 0:
            return number
    return len(lines)


def js_definitions(lines):
    """Classes and their methods, function declarations at any depth (a nested one indented), and the arrow functions
    and constants a module names at its top level (an arrow inside a function is a lambda, not a definition)."""
    found = []
    classes = []   # (indent, name, end) of the classes still open at the line
    for number, line in enumerate(lines, 1):
        indent = len(line) - len(line.lstrip())
        classes = [c for c in classes if c[2] >= number]
        holder = [c for c in classes if c[0] < indent][-1:]
        for pattern, kind in ((JS_CLASS, "class"), (JS_FUNCTION, "function"), (JS_ARROW, "const =>"),
                              (JS_METHOD, "method"), (JS_CONST, "const")):
            match = pattern.match(line)
            if not match or (pattern is JS_METHOD and (match.group(2) in NOT_METHODS or not holder)) \
                    or (pattern is JS_ARROW and indent):
                continue
            name = match.group(2)
            end = js_end(lines, number)
            qualified = (holder[0][1] + "." + name) if pattern is JS_METHOD else name
            found.append(Definition(kind, qualified, number, end, 1 if indent else 0))
            if kind == "class":
                classes.append((indent, name, end))
            break
    return found


# --- Markdown -----------------------------------------------------------------------------------------------------------

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def md_definitions(lines):
    headings = []
    fenced = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if match:
            headings.append((number, len(match.group(1)), match.group(2)))
    found = []
    for k, (number, level, title) in enumerate(headings):
        end = next((n - 1 for n, lv, _t in headings[k + 1:] if lv <= level), len(lines))
        while end > number and not lines[end - 1].strip():
            end -= 1
        found.append(Definition("#" * level, title, number, end, level - 1))
    return found


# --- the commands -------------------------------------------------------------------------------------------------------

def definitions(path):
    text, lines = read_lines(path)
    lower = path.lower()
    if lower.endswith(".py"):
        return "python", python_definitions(path, text), lines
    if lower.endswith(JS_SUFFIXES):
        return "js", js_definitions(lines), lines
    if lower.endswith(MD_SUFFIXES):
        return "md", md_definitions(lines), lines
    raise Failure("no outline for %s: Python, JavaScript (.js .mjs .cjs) and markdown only; use Grep with head_limit, "
                  "or Read with offset+limit" % shown(path), 2)


def outline(path, out):
    language, found, lines = definitions(path)
    if language == "python":
        found = python_outline(found)
    elif language == "md":
        for d in found:
            d.depth = len(d.kind) - 1
    out.write("%s: %d lines, %d entries (first-last line)" % (shown(path), len(lines), len(found)))
    for d in found:
        out.write(d.label() if language != "md" else "%d-%d %s%s %s" % (d.start, d.end, "  " * d.depth, d.kind, d.qualified))
    out.close("Read the rest with Read and offset+limit, or show one name.")


def _key(text):
    return text.strip().strip("`").lower()


def find(found, language, wanted):
    """The one definition `wanted` names, or Failure listing the candidates or the nearest names. A name that is one
    definition's whole name is that one; else every definition of that last name (or heading holding the words) is a
    candidate, and `@LINE` picks the one starting on that line, else the innermost holding it."""
    at = None
    match = re.match(r"^(.*)@(\d+)$", wanted)
    if match:
        wanted, at = match.group(1), int(match.group(2))
    if language == "md":
        exact = [d for d in found if _key(d.qualified) == _key(wanted)]
        named = [d for d in found if _key(wanted) in _key(d.qualified)]
    else:
        exact = [d for d in found if d.qualified == wanted]
        named = [d for d in found if d.name == wanted]
    if len(exact) == 1 and at is None:
        return exact[0]
    hits = named or exact
    if at is not None:
        hits = [d for d in hits if d.start == at] or [d for d in hits if d.start <= at <= d.end][-1:]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        names = sorted({d.qualified for d in found} | {d.name for d in found})
        near = difflib.get_close_matches(wanted, names, n=8, cutoff=0.5)
        raise Failure("no %s here.%s" % (repr(wanted), (" Nearest: " + ", ".join(near)) if near else
                                           " Run `tools/code.py outline` on the file for its names."))
    listing = "\n".join("  " + d.label() for d in hits[:40])
    raise Failure("%s names %d definitions; say which, as %s:\n%s"
                  % (repr(wanted), len(hits), "a heading's full text or TITLE@LINE" if language == "md"
                     else "Class.name or name@LINE", listing))


def show(target, out):
    if "::" not in target:
        raise Failure("show takes FILE::NAME (a function, a class, Class.method, a constant, or a markdown heading)", 2)
    file_part, wanted = target.rsplit("::", 1)
    path = resolve(file_part)
    language, found, lines = definitions(path)
    d = find(found, language, wanted.strip())
    out.write("%s::%s  lines %d-%d of %d" % (shown(path), d.qualified, d.start, d.end, len(lines)))
    for number in range(d.start, d.end + 1):
        out.write("%d\t%s" % (number, lines[number - 1]))
    out.close("Read the rest with Read offset=%d limit=%d." % (d.start + out.lines - 1, d.end - d.start + 2 - out.lines))


def _code_files():
    for top in REF_DIRS:
        for folder, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith(REF_SUFFIXES):
                    yield os.path.join(folder, name)


def refs(name, most, out):
    if not name.strip():
        raise Failure("refs takes a name", 2)
    pattern = re.compile(r"(?<![\w$])" + re.escape(name.strip()) + r"(?![\w$])")
    shown_lines = 0
    total = 0
    files = set()
    for path in _code_files():
        try:
            if os.path.getsize(path) > REF_FILE_MOST:
                continue
            _text, lines = read_lines(path)
        except (OSError, Failure):
            continue
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                total += 1
                files.add(path)
                if shown_lines < most and out.write("%s:%d:%s" % (shown(path), number, line.strip())):
                    shown_lines += 1
    if total == 0:
        out.write("no line names %s under %s" % (repr(name), ", ".join(d + "/" for d in REF_DIRS)))
    elif total > shown_lines:
        out.stream.write("[%d of %d references shown, in %d files; --max N for more (at most %d), or Grep with a path]\n"
                         % (shown_lines, total, len(files), REFS_MOST))
    else:
        out.stream.write("[%d references in %d files]\n" % (total, len(files)))


def main(argv=None, stream=None):
    if stream is None:
        stream = sys.stdout
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="tools/code.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("outline", help="the definitions of a file with their lines").add_argument("file")
    commands.add_parser("show", help="one definition or section: FILE::NAME").add_argument("target")
    ref = commands.add_parser("refs", help="the lines naming a word in the code")
    ref.add_argument("name")
    ref.add_argument("--max", type=int, default=REFS_DEFAULT)
    args = parser.parse_args(argv)
    try:
        if args.command == "outline":
            outline(resolve(args.file), Output(stream))
        elif args.command == "show":
            show(args.target, Output(stream))
        else:
            most = max(1, min(args.max, REFS_MOST))
            refs(args.name, most, Output(stream, most_lines=most))
    except Failure as failure:
        stream.write(str(failure) + "\n")
        return failure.code
    return 0


def run(argv):
    """The output and exit status of one command, for a test."""
    stream = io.StringIO()
    code = main(argv, stream)
    return code, stream.getvalue()


if __name__ == "__main__":
    sys.exit(main())
