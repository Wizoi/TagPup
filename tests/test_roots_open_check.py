"""A library whose root this machine does not place is told of where it is opened: the MCP server's
tools answer with the message that names machine_roots.json and the line to add, instead of the store's
refusal on the first photo (tagpup.mcp.server.find_library, tagpup.services.roots.problem; the web
servers' gate is tests/test_roots_routes.py's). The tools that read no photo's path -- the journal --
still answer.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.mcp import server  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheMcpServerOpeningALibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_open_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.name = "photo_index"

    def call(self, tool, arguments):
        return asyncio.run(server.build().call_tool(tool, arguments))

    def test_a_placed_library_opens(self):
        self.assertEqual(self.name, os.path.splitext(os.path.basename(server.find_library(self.name).path))[0])
        self.assertIsNone(roots_service.problem(server.find_library(self.name)))

    def test_an_unplaced_one_is_refused_with_the_line_to_add(self):
        os.remove(config.machine_roots_path())
        with self.assertRaises(ToolError) as why:
            server.find_library(self.name)
        text = str(why.exception)
        self.assertIn("machine_roots.json", text)
        self.assertIn('"pictures"', text)

    def test_a_tool_that_reads_photos_answers_with_that_message(self):
        os.remove(config.machine_roots_path())
        with self.assertRaises(ToolError) as why:
            self.call("folders", {"library": self.name})
        self.assertIn("machine_roots.json", str(why.exception))

    def test_the_checks_say_it_too_not_an_exception_name(self):
        os.remove(config.machine_roots_path())
        for tool, arguments in (("checks", {"library": self.name}),
                                ("check", {"library": self.name, "name": "rooted_rows_convert"})):
            with self.assertRaises(ToolError) as why:
                self.call(tool, arguments)
            self.assertIn("machine_roots.json", str(why.exception), tool)

    def test_what_reads_no_photo_path_still_answers_so_the_owner_can_undo_what_put_the_library_there(self):
        os.remove(config.machine_roots_path())
        self.assertIsNotNone(server.find_library(self.name, photos=False))
        for tool, arguments in (("history", {"library": self.name}), ("sync_state", {"library": self.name})):
            self.call(tool, arguments)

    def test_a_map_that_cannot_be_read_is_the_same_sentence(self):
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            handle.write("{ nope")
        with self.assertRaises(ToolError) as why:
            server.find_library(self.name)
        self.assertIn("machine_roots.json", str(why.exception))


if __name__ == "__main__":
    unittest.main()
