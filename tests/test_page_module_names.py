"""What a page module may be called, said once (#165).

Three places decide it: the server serving a module (tagpup.web.app), the router
sending a module asked for without a library to the startup library
(tagpup.web.libraries.PAGE_MODULE), and the page tests' loader (tests/frontend/
harness.mjs, fileFor). They agreed, with nothing holding them together, so a module
named outside one would load in the tests and 404 in the browser, or the reverse. The
server's two now follow one pattern, `libraries.MODULE_NAME`; this holds the harness
to it, and every module the pages have to all three.
"""
import os
import re
import unittest

from tagpup.web import app, libraries

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "frontend", "harness.mjs")

#: Names that must be refused and names that must be served, by all three.
REFUSED = ["a.b", "a b", "..", "", "x/y", "caf" + chr(0xE9), "a%2Eb", "a:b", chr(92) + "x"]
SERVED = ["main", "tag-editor", "api", "faces_strip", "Grid2"]


def harness_patterns():
    """fileFor's two module patterns, as Python patterns: (common, own)."""
    with open(HARNESS, encoding="utf-8") as handle:
        source = handle.read()
    body = source[source.index("function fileFor("):]
    body = body[:body.index(chr(10) + "}")]
    found = re.findall(r"pathname\.match\(/(.+?)/\);", body)
    if len(found) != 2:
        raise AssertionError("fileFor no longer has its two patterns: %r" % found)
    # A JavaScript regex without the u flag reads \w as ASCII, as re.ASCII does.
    return [re.compile(p.replace(chr(92) + "/", "/"), re.ASCII) for p in found]


def harness_loads(url_path):
    common, own = harness_patterns()
    return bool(common.match(url_path) or own.match(url_path))


class OneModuleName(unittest.TestCase):
    def test_the_server_serves_by_the_routers_pattern(self):
        self.assertIs(app.MODULE_NAME, libraries.MODULE_NAME)
        self.assertIn(libraries.MODULE_NAME.pattern.strip("^$"), libraries.PAGE_MODULE.pattern)

    def test_the_three_agree_on_names_to_refuse_and_serve(self):
        for name in REFUSED + SERVED:
            served = bool(app.MODULE_NAME.match(name))
            with self.subTest(name=name):
                self.assertEqual(served, name in SERVED)
                for url_path in ("/" + name + ".js", "/common/" + name + ".js"):
                    self.assertEqual(bool(libraries.PAGE_MODULE.match(url_path)), served, url_path)
                    self.assertEqual(harness_loads(url_path), served, url_path)

    def test_every_module_the_pages_have_is_named_so(self):
        seen = 0
        for folder, prefix in (("tagpup", "/"), ("tuner", "/"), ("common", "/common/")):
            for entry in os.listdir(os.path.join(ROOT, "web", folder)):
                if not entry.endswith(".js"):
                    continue
                seen += 1
                name = entry[:-3]
                with self.subTest(module=folder + "/" + entry):
                    self.assertTrue(app.MODULE_NAME.match(name))
                    self.assertTrue(libraries.PAGE_MODULE.match(prefix + entry))
                    self.assertTrue(harness_loads(prefix + entry))
        self.assertGreater(seen, 20)


if __name__ == "__main__":
    unittest.main()
