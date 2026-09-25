"""Edit source files from a patch script: exact, counted, and in the file's own newlines.

Every change in this repository is made by a short script, since shell heredocs mangle
backslashes (CLAUDE.md). Each script used to carry its own replace-and-assert helper,
and the helpers failed in the same three ways: a trailing blank the Write tool had
dropped made the old text match nothing, CRLF files came back LF, and a text found
twice was changed twice. Some three hundred scripts in two days; this is the one
helper.

    import sys; sys.path.insert(0, r"C:\\src\\kidzi\\GitHub\\TagPup\\tools")
    from patch import Patch

    with Patch("tagpup/web/app.py") as p:
        p.replace("old text", "new text")               # exactly once, or it fails
        p.replace("x = 1", "x = 2", count=3)             # exactly three times
        p.between("def start(", "def stop(", "...")      # from start up to (not incl.) end
        p.after("import os\\n", "import sys\\n")
        p.before("class Thing", "# a comment\\n")

Nothing is written unless every edit in the block succeeded. Write the old and new
text with plain newlines; they are put in the file's own. When the old text is not
found as written, it is looked for line by line with trailing blanks ignored.
"""
import os

CR, LF = chr(13), chr(10)


class PatchError(AssertionError):
    pass


class Patch:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8", newline="") as handle:
            self.text = handle.read()
        self.original = self.text
        self.eol = CR + LF if CR + LF in self.text else LF

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None and self.text != self.original:
            with open(self.path, "w", encoding="utf-8", newline="") as handle:
                handle.write(self.text)
        return False

    def _eol(self, s):
        return s.replace(CR + LF, LF).replace(LF, self.eol)

    def _fail(self, what, found, expected):
        raise PatchError("%s: %r found %d time(s), expected %d" % (self.path, what[:120], found, expected))

    def _spans(self, old):
        """Where `old` is: exact first, then line by line ignoring trailing blanks."""
        target = self._eol(old)
        spans, start = [], 0
        while True:
            i = self.text.find(target, start)
            if i < 0:
                break
            spans.append((i, i + len(target)))
            start = i + len(target)
        if spans or not old.endswith(LF):
            return spans
        want = [line.rstrip() for line in old.replace(CR + LF, LF).split(LF)[:-1]]
        lines = self.text.split(self.eol)
        offsets, pos = [], 0
        for line in lines:
            offsets.append(pos)
            pos += len(line) + len(self.eol)
        for i in range(len(lines) - len(want) + 1):
            if [line.rstrip() for line in lines[i:i + len(want)]] == want:
                end = offsets[i + len(want)] if i + len(want) < len(offsets) else len(self.text)
                spans.append((offsets[i], end))
        return spans

    def replace(self, old, new, count=1):
        spans = self._spans(old)
        if len(spans) != count:
            self._fail(old, len(spans), count)
        new = self._eol(new)
        for start, end in reversed(spans):
            self.text = self.text[:start] + new + self.text[end:]
        return self

    def between(self, start, end, new):
        """Replace from `start` (included) up to `end` (excluded); each must occur once
        in the file, `end` after `start`."""
        first = self._spans(start)
        if len(first) != 1:
            self._fail(start, len(first), 1)
        s = first[0][0]
        e = self.text.find(self._eol(end), first[0][1])
        if e < 0:
            self._fail(end, 0, 1)
        self.text = self.text[:s] + self._eol(new) + self.text[e:]
        return self

    def after(self, anchor, text):
        spans = self._spans(anchor)
        if len(spans) != 1:
            self._fail(anchor, len(spans), 1)
        e = spans[0][1]
        self.text = self.text[:e] + self._eol(text) + self.text[e:]
        return self

    def before(self, anchor, text):
        spans = self._spans(anchor)
        if len(spans) != 1:
            self._fail(anchor, len(spans), 1)
        s = spans[0][0]
        self.text = self.text[:s] + self._eol(text) + self.text[s:]
        return self


def new_file(path, text):
    """Write a new file, LF, refusing to overwrite one that exists."""
    if os.path.exists(path):
        raise PatchError("%s exists; patch it instead" % path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
