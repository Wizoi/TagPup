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
gets written.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))


def offered_tags(sugg_info, threshold=0.0):
    """The selection Apply All makes, lifted from the handler."""
    offered = list(sugg_info.get("tags") or [])
    offered_people = list(sugg_info.get("people") or [])
    apply_tags = [t["tag"] for t in offered if t.get("score", 0.0) >= threshold]
    apply_tags += [p["name"] for p in offered_people if p.get("score", 0.0) >= threshold]
    return apply_tags


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


class TestTheHandlerUsesTheOfferedLists(unittest.TestCase):
    """The guard. The drift was invisible for as long as the write threshold sat above
    the display floor, and would be again if someone reached back for raw_suggestions."""

    def test_auto_apply_does_not_read_raw_suggestions(self):
        source = os.path.join(WORKSPACE_DIR, "scripts", "tagpup_server.py")
        with open(source, encoding="utf-8") as f:
            text = f.read()

        start = text.index("def handle_post_folder_auto_apply")
        end = text.index("\n    def ", start + 10)
        # The comments explain the old behaviour by name, so check the code alone.
        body = "\n".join(line for line in text[start:end].split("\n")
                         if not line.strip().startswith("#"))

        self.assertNotIn(
            'raw_suggestions', body,
            "auto-apply is reading the unfiltered list again; it must write what the "
            "panel was shown, which is sugg_info['tags'] and sugg_info['people']")
        self.assertIn('sugg_info.get("tags")', body)
        self.assertIn('sugg_info.get("people")', body)


if __name__ == "__main__":
    unittest.main()
