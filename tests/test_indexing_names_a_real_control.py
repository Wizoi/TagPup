"""Adding a folder names the control that names faces from tags, and that control exists.

The messages told the owner to "run Recluster". No page has had a Recluster button since
TagTuner lost the route no page called (docs/findings.md, #8 and #22). Faces are named from
the photos' tags by the button "Name faces from tags" of TagTuner's header (and its gear) and of
TagPup's folder view (#789), or by `tagpup_cli.py faces-from-tags` and `cluster-faces`.

When some of the new faces have a likely name the sentence offers Re-examine on their folder, and
both pages make that word an action (web/common/reexamine-offer.js: TagPup's status line links to
TagTuner's Re-examine, TagTuner's index line runs it): the pages find it by the sentence's words,
so those words are held to the pages' here.
"""
import os
import re
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

from tagpup.core.result import Result  # noqa: E402
from tagpup.services import indexing  # noqa: E402


class AddingAFolderNamesARealControl(unittest.TestCase):
    def messages(self):
        """What adding a folder says of the faces it made when their photos carry a person's keyword that no face has:
        it offers to name them from the photos' tags (indexing.new_faces_said; the whole run:
        test_new_faces_are_matched_after_an_index)."""
        return [indexing.new_faces_said(Result(attempted=3, details={"faces": decided, "keyword_photos": 2}))
                for decided in (0, 1)]

    def test_no_message_names_a_button_that_is_gone(self):
        for message in self.messages():
            self.assertNotIn("Recluster", message)

    def test_each_names_the_button_both_apps_have(self):
        for page in ("tuner", "tagpup"):
            with open(os.path.join(WORKSPACE_DIR, "web", page, "index.html"), encoding="utf-8") as f:
                buttons = set(re.findall(r">([^<>]+)</button>", f.read()))
            self.assertIn(indexing.CLUSTERING_BUTTON, buttons, "the %s page has no such button" % page)
        for message in self.messages():
            self.assertIn(indexing.CLUSTERING_BUTTON, message)


class TheReexamineItOffersIsThePagesAction(unittest.TestCase):
    def offer(self):
        with open(os.path.join(WORKSPACE_DIR, "web", "common", "reexamine-offer.js"), encoding="utf-8") as f:
            found = re.search(r"export const REEXAMINE_OFFER = '([^']+)';", f.read())
        self.assertIsNotNone(found, "web/common/reexamine-offer.js no longer says REEXAMINE_OFFER as this test reads it")
        return found.group(1)

    def said(self, looked, decided):
        return indexing.new_faces_said(Result(attempted=looked, details={"faces": decided, "keyword_photos": 0}))

    def test_new_faces_with_a_likely_name_offer_it_in_the_words_the_pages_link(self):
        self.assertIn(self.offer(), self.said(5, 3))

    def test_no_face_with_a_likely_name_offers_nothing_to_press(self):
        for looked in (0, 4):
            self.assertNotIn("Re-examine", self.said(looked, 0))


if __name__ == "__main__":
    unittest.main()
