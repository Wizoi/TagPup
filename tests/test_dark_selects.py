"""Every drop-down list on every page is dark, from one rule (#782).

The library picker's white strip came back after beaadb2 fixed it in each page's own stylesheet: a rule per page is a
rule a page can lose, and a new page or a select made later has none. web/common/dark-select.css is the one place,
linked by every page; a stylesheet of a page styles a select's look and never the scheme of its list. What the open list
looks like is checked in a real browser by scripts/measure_select_list.py; this holds the rule to every page.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PAGES = ("tagpup", "tuner", "activity")
COMMON = "dark-select.css"


def read(*parts):
    with open(os.path.join(WEB, *parts), encoding="utf-8") as handle:
        return handle.read()


class DarkSelects(unittest.TestCase):
    def test_every_page_links_the_one_rule(self):
        for page in PAGES:
            links = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', read(page, "index.html"))
            self.assertIn("common/" + COMMON, links, "%s does not link web/common/%s" % (page, COMMON))

    def test_the_rule_is_for_every_select_and_its_options_are_the_selects_colour(self):
        css = read("common", COMMON)
        self.assertRegex(css, r"(?m)^select\s*\{[^}]*color-scheme:\s*dark;")
        options = re.search(r"select option,\s*select optgroup\s*\{([^}]*)\}", css)
        self.assertIsNotNone(options)
        self.assertIn("background-color: inherit", options.group(1))
        self.assertIn("color: inherit", options.group(1))

    def test_no_other_stylesheet_or_tag_chooses_a_scheme_or_an_options_colour(self):
        # A second place that styles the list is the second fix for one symptom.
        for folder in PAGES + ("common",):
            for name in sorted(os.listdir(os.path.join(WEB, folder))):
                if not name.endswith((".css", ".html")) or name == COMMON:
                    continue
                text = re.sub(r"/\*.*?\*/", "", read(folder, name), flags=re.S)
                self.assertNotIn("color-scheme", text, "%s/%s sets a color-scheme" % (folder, name))
                if name.endswith(".css"):
                    self.assertIsNone(re.search(r"(?<![\w-])option(?![\w-])[^{}]*\{", text),
                                      "%s/%s styles an option" % (folder, name))

    def test_no_select_opts_out_of_the_rule_in_its_markup(self):
        for page in PAGES:
            for tag in re.findall(r"<select\b[^>]*>", read(page, "index.html")):
                style = re.search(r'style="([^"]*)"', tag)
                self.assertNotRegex(style.group(1) if style else "", r"color-scheme|appearance",
                                    "%s: %s" % (page, tag))

    def test_the_rule_is_served_to_each_page(self):
        for kind, url in (("tagpup", "/library/common/%s"), ("tuner", "/library/common/%s"), ("tagpup", "/activity/common/%s")):
            app, _home = web_client.app_for(self, kind)
            reply = app.test_client().get(url % COMMON)
            self.assertEqual(200, reply.status_code, (kind, url))
            self.assertIn(b"color-scheme: dark", reply.data)


if __name__ == "__main__":
    unittest.main()
