"""tagpup_web.py --db with a name no library may have serves nothing, and says why.

Making a library returns a Result, refused for a name the library-name rule forbids --
a space or a dot, as in `Harbour Photos`. The launcher ignored it: it logged "making
one", made nothing, and served a library that was not there, every request failing with
nothing naming the cause. Found in review of 3c04892.
"""
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import own_home  # noqa: E402

import tagpup_web  # noqa: E402


class TheLauncher(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        environ = mock.patch.dict(os.environ, {"TAGPUP_WEB_NO_WARMUP": "1"})
        environ.start()
        self.addCleanup(environ.stop)
        serve = mock.patch.object(tagpup_web.web, "serve")
        self.serve = serve.start()
        self.addCleanup(serve.stop)
        to_file = mock.patch.object(tagpup_web.logs, "to_file", return_value="(no log file)")
        to_file.start()
        self.addCleanup(to_file.stop)

    def test_a_name_the_rule_refuses_is_not_served(self):
        with self.assertLogs("tagpup_web", level="ERROR") as logged:
            code = tagpup_web.main(["--db", "Harbour Photos", "--tagpup-port", "1", "--tuner-port", "2"])
        self.assertNotEqual(0, code)
        self.serve.assert_not_called()
        self.assertEqual([], os.listdir(self.home.data), "a library was made")
        self.assertTrue(any("letters, numbers, underscores and hyphens" in line for line in logged.output),
                        logged.output)

    def test_a_name_it_may_make_is_made_and_served(self):
        logging.getLogger("tagpup_web").setLevel(logging.INFO)
        code = tagpup_web.main(["--db", "harbour", "--tagpup-port", "1", "--tuner-port", "2"])
        self.assertEqual(0, code)
        self.serve.assert_called_once()
        self.assertTrue(os.path.exists(self.home.library("harbour.db")))


if __name__ == "__main__":
    unittest.main()
