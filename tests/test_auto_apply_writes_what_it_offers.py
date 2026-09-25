"""Apply All writes the suggestions the page was shown, and no others.

Reported from the running app: a photo with no people tags came back from Apply All
carrying seven, two of whom the suggestions panel had never mentioned -- while the
five it *had* listed were still sitting there unapplied.

Two lists, built in two places, drifting. The panel is served `tags` and `people`,
which the server filters to a score of 0.6 or better. Apply All read
`raw_suggestions`, which is everything the suggester produced down to its own floor,
and wrote that instead. As long as the write threshold was 0.75 the difference was
invisible, because 0.75 is above 0.6. Lowering it to nothing -- so that Apply All
would stop leaving suggestions behind -- exposed the whole gap at once.

The fix is not a matching threshold. It is one list: what the page was shown is what
gets written. The selection is tagpup.core.suggesting.offered_tags, and the route
calls it.
"""
import inspect
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core.suggesting import offered_tags  # noqa: E402

#: What the server sends the page: `tags` and `people` filtered to >= 0.6, plus the
#: unfiltered `raw_suggestions` it keeps for itself.
SUGGESTION = {
    "tags": [{"tag": "Cross Country", "score": 0.91},
             {"tag": "Kentridge", "score": 0.64}],
    "people": [{"name": "People/Sonja Grimaldi", "score": 0.88},
               {"name": "People/Kira Bao", "score": 0.72}],
    "raw_suggestions": {"suggested_tags": [
        {"tag": "Cross Country", "score": 0.91},
        {"tag": "Kentridge", "score": 0.64},
        {"tag": "People/Sonja Grimaldi", "score": 0.88, "has_face_match": True},
        {"tag": "People/Kira Bao", "score": 0.72, "has_face_match": True},
        # Below the panel's floor, so never shown. These are the two that turned up
        # on the photo without explanation.
        {"tag": "People/Rosalind Keye", "score": 0.44, "has_face_match": True},
        {"tag": "People/Emory Kade", "score": 0.31, "has_face_match": True},
    ]},
}


class TestItWritesWhatWasOffered(unittest.TestCase):
    def test_everything_the_panel_listed_is_written(self):
        written = offered_tags(SUGGESTION)
        for expected in ("Cross Country", "Kentridge",
                         "People/Sonja Grimaldi", "People/Kira Bao"):
            self.assertIn(expected, written)

    def test_nothing_the_panel_withheld_is_written(self):
        # The report: two people appeared on the photo that the panel never mentioned.
        written = offered_tags(SUGGESTION)
        self.assertNotIn("People/Rosalind Keye", written)
        self.assertNotIn("People/Emory Kade", written)

    def test_people_are_written_as_well_as_keywords(self):
        # They arrive in a separate list, and reading only `tags` would silently skip
        # every person -- the opposite failure, and just as quiet.
        written = offered_tags(SUGGESTION)
        self.assertEqual(
            sorted(w for w in written if w.startswith("People/")),
            ["People/Kira Bao", "People/Sonja Grimaldi"])

    def test_the_count_matches_what_the_panel_shows(self):
        shown = len(SUGGESTION["tags"]) + len(SUGGESTION["people"])
        self.assertEqual(len(offered_tags(SUGGESTION)), shown)

    def test_a_threshold_still_narrows_it_if_one_is_sent(self):
        written = offered_tags(SUGGESTION, threshold=0.8)
        self.assertEqual(sorted(written), ["Cross Country", "People/Sonja Grimaldi"])

    def test_a_photo_with_nothing_offered_writes_nothing(self):
        self.assertEqual(offered_tags({"tags": [], "people": []}), [])

    def test_a_suggestion_record_missing_its_lists_is_not_a_crash(self):
        self.assertEqual(offered_tags({}), [])
        self.assertEqual(offered_tags({"tags": None, "people": None}), [])


class TestTheRouteUsesTheOfferedLists(unittest.TestCase):
    """The guard. The drift was invisible for as long as the write threshold sat above
    the display floor, and would be again if someone reached back for raw_suggestions."""

    def test_auto_apply_does_not_read_raw_suggestions(self):
        from tagpup.web import tagpup_routes

        # The comments explain the old behaviour by name, so check the code alone.
        body = "\n".join(line for line in inspect.getsource(tagpup_routes.folder_auto_apply).split("\n")
                         if not line.strip().startswith("#"))
        self.assertNotIn(
            "raw_suggestions", body,
            "auto-apply is reading the unfiltered list again; it must write what the "
            "panel was shown, which offered_tags selects from entry['tags'] and entry['people']")
        self.assertIn("offered_tags(", body)

    def test_the_selection_reads_only_the_shown_lists(self):
        import ast
        import textwrap

        function = ast.parse(textwrap.dedent(inspect.getsource(offered_tags))).body[0]
        # Its docstring names the old behaviour; the code must not.
        keys = [node.value for node in ast.walk(function)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node is not function.body[0].value]
        self.assertNotIn("raw_suggestions", keys)
        self.assertIn("tags", keys)
        self.assertIn("people", keys)


if __name__ == "__main__":
    unittest.main()
