"""A suggested tag is shown, applied and written from one score:
tagpup.core.suggesting.OFFER_A_TAG (docs/findings.md, #70).

TagPup showed a tag from 0.6, while the CLI's `write`, the writer and the runner wrote
from 0.5 -- tags the app never showed, right 45% of the time when measured.
"""
import inspect
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from tagpup.core import suggesting  # noqa: E402


def source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class OneValue(unittest.TestCase):
    def test_the_cli_writes_from_it(self):
        import tagpup_cli
        option = next(p for p in tagpup_cli.write.params if p.name == "min_score")
        self.assertEqual(suggesting.OFFER_A_TAG, option.default)

    def test_the_writer_writes_from_it(self):
        import tagpup_cli
        from tagpup.services import tagging
        for writer in (tagpup_cli.write_suggestions_file, tagging.suggestion_writes, suggesting.written_tags):
            default = inspect.signature(writer).parameters["min_score"].default
            self.assertEqual(suggesting.OFFER_A_TAG, default, writer.__name__)

    def test_the_runner_starts_from_it(self):
        self.assertIn('insert(0, "%.2f" % suggesting.OFFER_A_TAG)', source("runner.py"))

    def test_no_score_is_compared_with_a_number_of_its_own(self):
        # Where a tag's score decides, it is compared with the one value: TagPup's
        # routes and the model that offers, the writer, the CLI and the runner.
        for name in ("tagpup/web/tagpup_routes.py", "tagpup/core/suggesting.py", "tagpup/jobs/suggestions.py",
                     "tagpup/services/tagging.py", "tagpup_cli.py", "runner.py"):
            found = re.findall(r"score[\w\"'\].)]*\s*>=\s*0\.\d", source(name))
            self.assertEqual([], found, name)
        # What a run offers (tagpup.services.suggester.SuggestionModel.offered). The
        # suggester's own scoring beside it has values of its own, consensus's floor
        # among them, which are not what a tag is offered from.
        from tagpup.services.suggester import SuggestionModel
        found = re.findall(r"score[\w\"'\].)]*\s*>=\s*0\.\d", inspect.getsource(SuggestionModel))
        self.assertEqual([], found, "SuggestionModel")


if __name__ == "__main__":
    unittest.main()
