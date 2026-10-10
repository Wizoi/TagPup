"""Importing the web app does not load the heavy libraries.

scikit-learn and scipy took 1.2 of the 2.1 seconds the web app took to import, and more
than a hundred test files import the app. They are imported inside the function that uses
them (tagpup.ml.grouping.cluster_candidates). The models (torch, open_clip) and the face
library are the graphics card's single owner's, and load only when a model is asked for.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import processes  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEAVY = ("sklearn", "scipy.stats", "torch", "open_clip", "onnxruntime", "insightface", "cv2", "pandas")

PROBE = ("import sys, tagpup.web.app\n"
         "loaded = [m for m in %r if m in sys.modules]\n"
         "print('LOADED=' + ','.join(loaded))\n") % (HEAVY,)


class HeavyImportsStayLazy(unittest.TestCase):
    def test_importing_the_web_app_loads_none_of_them(self):
        done = processes.run([sys.executable, "-c", PROBE], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        self.assertEqual(0, done.returncode, done.stderr)
        line = [ln for ln in done.stdout.splitlines() if ln.startswith("LOADED=")][-1]
        self.assertEqual("LOADED=", line, "the web app's import loaded " + line[len("LOADED="):])


if __name__ == "__main__":
    unittest.main()
