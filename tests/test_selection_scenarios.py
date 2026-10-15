"""Scenarios V7 and V5: Select all minus the excluded, and the counts the navigator and the folder picker show.

V7: the owner selects a source (the whole library, a keyword, a person, a year, a folder), un-ticks a few cards and opens the
panel or edits in bulk. The selection panel's tally (tags, people and the folders to open in Organize, #675) and the bulk
edits' resolver (tagpup.services.selection.resolve) read the same photos as the ids route minus the excluded, under the
caps: batches.MAX_BATCH (what a job takes; a tally may be larger) and selection.MAX_LISTED (what a request may name).
V5: the navigator counts the photos under a node, each once (a person's group, #673), and the folder picker counts the
photos below a folder the way the navigator does.

Lifted from tests/test_selection_folders.py (#675), test_selection_tally.py (#865 stays cited by the order and cap tests that
remain there), test_navigator_people_groups.py (#673) and test_folder_picker_counts_alike.py, and what each catches:
  Lifted: the tally ignoring `excluded` (selection.tally passing () to store.tally) fails the tally against the list.
  Lifted: the folders listed above MAX_FOLDERS_LISTED (`len(counted) > MAX_FOLDERS_LISTED` -> `False`, #675) fails
          the folders count and name.
  Lifted: the size limit `count > batches.MAX_BATCH` as `>=` fails the boundary at the cap.
  Lifted: the navigator rolling a person up to the nearest branch only (`reached.update(upward[parent][:1])`, #673) fails
          the groups' counts.
  Lifted: the picker's `indexed` counting only the photos directly in a folder (the old bug: "8756 image(s)" for a folder
          fully indexed) fails the picker against the navigator.

Photos are rows as the indexer records them (tests/view_library.py); fictional names only.
"""
import os
import sys
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tuner_client  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import batches, library_view, selection  # noqa: E402

WREN, ROWAN = "People/Wren Halloway", "People/Rowan Thackeray"
ODD = "Bake & Share #2 (Rowan's)"


class TheSelectionPanel(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", "Trips/Lakes", "Activity/Sailing", "People/Wren Halloway", "People/Rowan Thackeray",
                     face_root="People")
        photo = self.vl.photo
        self.a = photo("2024 Coast", "a.jpg", "2024:06:01 10:00:00", tags=["Trips/Coast", WREN])
        self.b = photo("2024 Coast", "b.jpg", "2024:06:02 10:00:00", tags=["Trips/Coast", "Activity/Sailing", WREN, ROWAN])
        self.c = photo("2023 Lakes", "c.jpg", "2023:01:02 10:00:00", tags=["Trips/Lakes"])
        self.d = photo("Misc", "d.jpg", tags=[ROWAN, "Not In The Tree"])
        self.e = photo("Misc", "e.jpg")
        self.f = photo(ODD, "f.jpg", "2024:07:02 10:00:00", tags=["Trips/Coast"])
        self.vl.forget("Not In The Tree")   # a keyword read before the index made nodes: it has none
        self.everyone = [self.a, self.b, self.c, self.d, self.e, self.f]
        self.library = self.vl.library

    def post(self, selection_body, route="selection/tally", **kwargs):
        return self.client.post("/library/api/library/" + route, json={"selection": selection_body}, **kwargs)

    def tally(self, selection_body):
        reply = self.post(selection_body)
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def ids(self, source):
        query = {"kind": source["kind"], "value": source.get("value"), "folder": source.get("value"),
                 "recursive": "1" if source.get("recursive") else ""}
        return self.client.get("/library/api/library/ids", query_string=query).get_json()["ids"]

    def folder(self, name):
        return paths.stored(os.path.join(self.vl.pictures, name))

    def test_select_all_minus_the_excluded_is_the_same_photos_for_the_tally_the_folders_and_a_bulk_edit(self):
        sources = ({"kind": "all"}, {"kind": "keyword", "value": "Trips"}, {"kind": "person", "value": "Rowan Thackeray"},
                   {"kind": "year", "value": "2024"}, {"kind": "month", "value": "2024-06"},
                   {"kind": "folder", "value": os.path.join(self.vl.pictures, "Misc"), "recursive": True})
        for named in sources:
            held = self.ids(named)
            left_out = held[:1] + [999999] + [each for each in self.everyone if each not in held][:1]   # a card, nobody's, another
            wanted = [each for each in held if each not in left_out]
            with self.subTest(source=named):
                listed = self.tally({"ids": wanted})
                self.assertEqual(listed, self.tally({"source": named, "excluded": left_out}), "the source minus the excluded")
                self.assertEqual(len(wanted), listed["total"])
                self.assertEqual(len(wanted), sum(each["photos"] for each in listed["folders"]["listed"]))
                resolved = selection.resolve(self.library, selection.read(self.library, {"source": named, "excluded": left_out}))
                self.assertEqual(wanted, resolved.ids, "a bulk edit writes the photos the panel counted, in the ids' order")
                self.assertEqual(len(held) - len(wanted), resolved.excluded)

    def test_the_tally_counts_a_photo_once_and_a_person_is_not_a_keyword(self):
        found = self.tally({"ids": self.everyone + [self.a, 9999]})
        self.assertEqual(6, found["total"], "an id named twice, and one nobody has, count once or not at all")
        self.assertEqual({"Trips/Coast": 3, "Trips/Lakes": 1, "Activity/Sailing": 1},   # #865: people are not among the tags
                         {each["tag"]: each["count"] for each in found["tags"]})
        self.assertEqual({"Wren Halloway": 2, "Rowan Thackeray": 2}, {each["name"]: each["count"] for each in found["people"]})
        self.assertNotIn("Not In The Tree", str(found), "a keyword no node holds is not tallied")
        self.assertEqual((0, [], []), tuple(self.tally({"ids": []})[key] for key in ("total", "tags", "people")))
        self.assertEqual(self.tally({"source": {"kind": "all"}, "excluded": self.everyone})["folders"], {"count": 0, "listed": []})

    def test_a_branch_of_the_tree_is_a_keyword_and_never_a_person_to_open(self):
        self.vl.tree("People/Family/Parent", face_root="People")
        branch = self.vl.photo("Misc", "g.jpg", tags=["People/Family", "People/Family/Parent"])
        found = self.tally({"ids": [self.a, branch]})
        self.assertEqual({"Wren Halloway": True, "Parent": True}, {each["name"]: each["has_node"] for each in found["people"]})
        tags = {each["tag"] for each in found["tags"]}
        self.assertIn("People/Family", tags, "a branch is a keyword")
        self.assertNotIn("People/Family/Parent", tags)

    def test_the_folders_of_a_selection_are_named_up_to_ten_and_counted_beyond(self):
        """#675: what the panel offers to open in Organize."""
        listed = self.tally({"ids": [self.a, self.b, self.f]})["folders"]
        self.assertEqual(2, listed["count"])
        self.assertEqual([{"path": self.folder("2024 Coast"), "name": "2024 Coast", "photos": 2},
                          {"path": self.folder(ODD), "name": ODD, "photos": 1}], listed["listed"])
        more = [self.vl.photo("Event %02d" % n, "p.jpg") for n in range(11)]
        self.assertEqual({"count": 11, "listed": []}, self.tally({"ids": more})["folders"])
        self.assertEqual(["Event %02d" % n for n in range(10)], [each["name"] for each in self.tally({"ids": more[:10]})["folders"]["listed"]])
        whole = self.tally({"source": {"kind": "all"}})["folders"]
        self.assertEqual(4 + 11, whole["count"], "the whole library is one grouped read")
        self.assertEqual([], whole["listed"])

    def test_the_caps_a_job_takes_and_a_request_may_name(self):
        with mock.patch.object(batches, "MAX_BATCH", 4):
            everything = {"source": {"kind": "all"}}
            self.assertEqual(6, self.tally(everything)["total"], "a panel may tally what a job would refuse")
            for body, expected in (({"ids": self.everyone[:4]}, 4), ({"source": {"kind": "all"}, "excluded": self.everyone[:2]}, 4)):
                self.assertEqual(expected, len(selection.resolve(self.library, selection.read(self.library, body)).ids), body)
            for body in ({"ids": self.everyone[:5]}, {"source": {"kind": "all"}, "excluded": self.everyone[:1]},
                         {"source": {"kind": "all"}, "excluded": [999999, 999998]}):   # excluded nobodies take none away
                with self.subTest(body=body), self.assertRaisesRegex(Refused, "Select up to 4 photos at a time"):
                    selection.resolve(self.library, selection.read(self.library, body))
        with mock.patch.object(selection, "MAX_LISTED", 3):
            for body in ({"ids": self.everyone[:4]}, {"source": {"kind": "all"}, "excluded": self.everyone[:4]}):
                reply = self.post(body)
                self.assertEqual(400, reply.status_code)
                self.assertIn("at most 3", reply.get_json()["error"])
                self.assertNotIn("Traceback", reply.get_json()["error"])

    def test_a_request_that_means_nothing_is_a_sentence_and_this_pc_only(self):
        for body in (None, {}, {"selection": None}, {"selection": {"ids": "x"}}, {"selection": {"source": {"kind": "nope"}}}):
            reply = self.client.post("/library/api/library/selection/tally", json=body)
            self.assertEqual(400, reply.status_code, body)
            self.assertNotIn("Traceback", reply.get_json()["error"])
        self.assertEqual(403, self.post({"ids": [self.a]}, environ_overrides={"REMOTE_ADDR": "10.0.0.7"}).status_code)

    def test_two_libraries_tally_their_own_photos_under_the_same_ids(self):
        other = ViewLibrary(self, "meadow", home=self.home)
        other.tree("Trips/Coast")
        mine = other.photo("Dunes", "x.jpg", "2024:06:01 10:00:00", tags=["Trips/Coast"])
        self.assertEqual(self.a, mine, "the two libraries number their photos alike")
        reply = self.client.post("/meadow/api/library/selection/tally", json={"selection": {"ids": [mine]}}).get_json()
        self.assertEqual(([{"tag": "Trips/Coast", "count": 1}], []), (reply["tags"], reply["people"]))
        self.assertEqual("Dunes", reply["folders"]["listed"][0]["name"])
        self.assertEqual("2024 Coast", self.tally({"ids": [self.a]})["folders"]["listed"][0]["name"])


class TheNavigatorAndThePickerCountAlike(unittest.TestCase):
    def test_people_are_grouped_under_the_branch_they_are_filed_in_each_photo_once(self):
        """#673: Family/Immediate apart from Friends, each branch with the photos naming anyone under it, each once."""
        rowan, hazel, wren, ash = "Rowan Thackeray", "Hazel Brookmire", "Wren Halloway", "Ash Gantry"
        vl = ViewLibrary(self)
        vl.tree("Family/Immediate/" + rowan, "Family/Cousins/" + hazel, face_root="Family")
        vl.tree("Friends/" + wren, "Friends/" + ash, face_root="Friends")
        vl.tree("Neighbours/" + ash, face_root="Neighbours")   # one name filed twice: two people
        vl.photo("A", "both.jpg", tags=["Family/Immediate/" + rowan, "Family/Cousins/" + hazel])
        vl.photo("A", "rowan.jpg", tags=["Family/Immediate/" + rowan])
        vl.photo("A", "wren.jpg", tags=["Friends/" + wren, "Family/Cousins/" + hazel])
        vl.photo("A", "ash.jpg", tags=["Friends/" + ash])
        vl.photo("A", "ash2.jpg", tags=["Neighbours/" + ash])
        found = library_view.navigator(vl.library, "people")
        self.assertEqual({rowan: "Family/Immediate", hazel: "Family/Cousins", wren: "Friends"},
                         {each["name"]: each["group"] for each in found["people"] if each["name"] != ash})
        self.assertEqual([("Friends/" + ash, "Friends", 1), ("Neighbours/" + ash, "Neighbours", 1)],
                         sorted((each["person"]["tag"], each["group"], each["count"]) for each in found["people"] if each["name"] == ash),
                         "two people called alike are two entries, each filed where their node is")
        self.assertEqual({"Family": ("Family", None, 3), "Family/Immediate": ("Immediate", "Family", 2),
                          "Family/Cousins": ("Cousins", "Family", 2), "Friends": ("Friends", None, 2),
                          "Neighbours": ("Neighbours", None, 1)},
                         {each["tag"]: (each["name"], each["parent"], each["count"]) for each in found["groups"]})
        self.assertEqual(0, found["unfiled"])
        vl.tree("Family/Immediate/" + rowan + "/Baby photos", face_root="Family")   # Rowan's node becomes a branch
        again = library_view.navigator(vl.library, "people")
        self.assertIsNone({each["name"]: each["group"] for each in again["people"]}[rowan], "a branch is nobody")
        self.assertNotIn("Family/Immediate", [each["tag"] for each in again["groups"]])
        empty = ViewLibrary(self, "empty")
        empty.photo("A", "x.jpg")
        self.assertEqual({"people": [], "groups": [], "unfiled": 0}, library_view.navigator(empty.library, "people"))

    def test_the_folder_picker_counts_the_photos_below_a_folder_as_the_navigator_does(self):
        vl = ViewLibrary(self)
        for folder, name in (("Season/Meet 1", "a.jpg"), ("Season/Meet 1", "b.jpg"), ("Season/Meet 2", "c.jpg"), ("", "own.jpg")):
            vl.photo(folder, name, real=True)
        navigator = {each["path"]: each for each in library_view.navigator(vl.library, "folders")["folders"]}
        listing = tuner_client.Requests(tuner_client.app_on(vl.path)).get(
            "/api/folder/subfolders?path=" + urllib.parse.quote(vl.pictures))
        season = next(each for each in listing["folders"] if each["name"] == "Season")
        self.assertEqual(navigator[season["path"]]["recursive"], season["indexed"], "photos below the folder, not only in it")
        self.assertEqual((3, 3), (season["images"], season["indexed"]))
        self.assertEqual((1, 1), (listing["own_images"], listing["own_indexed"]))
        self.assertEqual(navigator[listing["parent"]]["direct"], listing["own_indexed"])


if __name__ == "__main__":
    unittest.main()
