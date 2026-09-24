"""Each library keeps its own record of how clustering named its faces.

The trace went to face_resolution_trace.json beside the database: one file for every
library in the folder, so clustering one overwrote the record of another.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from faces import resolution_trace_path  # noqa: E402


class TraceFilePerLibrary(unittest.TestCase):
    def test_two_libraries_in_one_folder_have_two_traces(self):
        one = resolution_trace_path(os.path.join("libraries", "photo_index.db"))
        two = resolution_trace_path(os.path.join("libraries", "kr-track.db"))
        self.assertNotEqual(one, two)
        self.assertEqual(os.path.join("libraries", "kr-track_face_resolution_trace.json"), two)


if __name__ == "__main__":
    unittest.main()
