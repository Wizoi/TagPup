"""Every route that writes is classified against "Name faces from tags" (docs/INVARIANTS.md; Integration-and-apps review 2026-10-09, C13).

While the naming job writes names (or groups the faces of the whole library), the writes its plan and grouping would be written over
are answered 409 (tagpup.web.name_faces_routes.GUARDED, and TagTuner's own through tuner_routes.clustering_refusal). The list was
kept by hand, and four routes were missing from it. Now a new POST route fails here until it is guarded, or listed below with the
reason it need not be. The naming apply is itself guarded against the rows it read (journal `expect` on the face's name, tag and
exclusion and on the photo's tags, tagpup.services.faces_from_tags._edits), so a route is safe when it changes none of those, and
none of the faces' vectors or the people the plan read, and writes no photo file the job also writes (the job writes no file).
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_client  # noqa: E402

from tagpup.web import name_faces_routes  # noqa: E402

WRITES = {"POST", "PUT", "PATCH", "DELETE"}

#: Write routes that need no refusal while names are given, each with why. Keyed as Flask names the route.
ALLOWED = {
    # The job's own, and its neighbours' controls.
    "/api/name-faces/start": "the job itself; it refuses to start beside another job, and a second click is a 409 naming it",
    "/api/name-faces/confirm": "the job itself",
    "/api/name-faces/cancel": "the job itself",
    "/api/faces/job/cancel": "stops an assignment of faces after its step; writes nothing of its own",
    "/api/library/bulk/cancel": "stops a bulk edit after its step; writes nothing of its own",
    "/api/folder/suggest-cancel": "stops Suggest, which writes no name, face or tag",
    "/api/folder/suggest-start": "Suggest writes its own suggestion rows and rows for photos never read, no face name, tag or file; the job refuses to start beside it",
    # Reads asked with a body, or a command to the machine.
    "/api/library/ids": "a read (the ids of a selection), a POST for the size of its body",
    "/api/library/selection/delete-check": "a read: what deleting would take",
    "/api/library/selection/tally": "a read: counts of a selection",
    "/api/taxonomy/delete-check": "a read: what deleting a node would change",
    "/api/activity/attention/check": "re-reads what needs attention; changes no name, face, tag or file",
    "/api/activity/models/unload": "frees the models in memory; no data",
    "/api/photo/open": "opens the file in the viewer; no data",
    "/api/photo/open-explorer": "shows the file in Explorer; no data",
    "/api/server/drain": "the update's own; the job is work a drain waits for (lifecycle.long_work)",
    "/api/server/resume": "the update's own",
    # Writes that touch nothing the plan or the grouping reads.
    "/api/databases/create": "makes another library",
    "/api/settings": "the library's settings; they apply to the next index or Suggest, not to names already read",
    "/api/damaged-photos/check": "records which files cannot be read, in the damaged-photo marks only",
    "/api/sync/review/ignore": "marks a folder ignored in the review list; adds no photo (include does, and is guarded)",
    "/api/taxonomy/create": "adds a node to the tree; no face, photo or existing node changes",
    # The four the review found missing, decided from the code (the first three are not guarded; Undo and the rest now are).
    "/api/folder/time-shift": "writes Date Taken fields and the photo row's dates, mtime and size; the plan reads tags, faces, boxes and "
                              "vectors, no date, and the apply expects the face and photo-tags rows it read",
    "/api/folder/rename-photos": "renames files and re-points the photo row by id; faces, tags and vectors follow the photo id, "
                                 "which the plan and the apply are keyed by",
    "/api/photo/rotate": "turns the file's Orientation (a TIFF's boxes too) and drops the photo's vectors; no name, tag or "
                         "exclusion, which are the columns the apply expects; a name decided from the vectors stays true of the same pixels",
}


def rule_of(rule):
    """The route as `GUARDED` and `ALLOWED` key it: from "/api/", without the library the URL names."""
    return rule[rule.find("/api/"):]


def refuses_in_the_route(view):
    return "clustering_refusal" in inspect.getsource(view)


class EveryWriteRouteIsClassified(unittest.TestCase):
    def routes(self):
        found = {}
        for kind in ("tagpup", "tuner"):
            app, _home = web_client.app_for(self, kind)
            for rule in app.url_map.iter_rules():
                if not rule.methods & WRITES:
                    continue
                blueprint = rule.endpoint.split(".")[0]
                before = {each.__name__ for each in app.before_request_funcs.get(blueprint, [])}
                guarded = ("refuse_writes_while_clustering" in before or refuses_in_the_route(app.view_functions[rule.endpoint]))
                found.setdefault(rule_of(rule.rule), set()).add(guarded)
        return found

    def test_each_is_guarded_while_names_are_given_or_listed_with_a_reason(self):
        found = self.routes()
        unclassified = sorted(rule for rule, guarded in found.items()
                              if rule not in name_faces_routes.GUARDED and rule not in ALLOWED and guarded != {True})
        self.assertEqual([], unclassified, "add each to name_faces_routes.GUARDED, or to ALLOWED here with the reason it is safe")

    def test_the_lists_hold_no_route_that_is_gone_and_no_route_twice(self):
        found = self.routes()
        self.assertEqual([], sorted(set(ALLOWED) - set(found)), "allowed, but no such route")
        self.assertEqual([], sorted(name_faces_routes.GUARDED - set(found)), "guarded, but no such route")
        self.assertEqual([], sorted(set(ALLOWED) & name_faces_routes.GUARDED), "both guarded and allowed")
        self.assertTrue(all(ALLOWED.values()), "every allowed route has its reason")


if __name__ == "__main__":
    unittest.main()
