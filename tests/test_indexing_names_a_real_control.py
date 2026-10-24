"""Adding a folder names the control that names faces from tags, and that control exists.

The messages told the owner to "run Recluster". No page has had a Recluster button since
TagTuner lost the route no page called (docs/findings.md, #8 and #22). Faces are named from
the photos' tags by the button "Name faces from tags" of TagTuner's header (and its gear) and of
TagPup's folder view (#789), or by `tagpup_cli.py faces-from-tags` and `cluster-faces`.
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


if __name__ == "__main__":
    unittest.main()
