"""The models are built in one place, tagpup.runtime, from the settings it is given.

Each ClipEmbedder read config.ini for itself and shared its loaded model through a class
attribute; the suggester built its own face model into a module-level slot; the web
launcher filled another slot in tagpup.jobs.suggestions from a script (docs/findings.md,
#112). Nobody could say which settings a model in the process had been built from, and a
test that reached one loaded gigabytes of weights. A model is now built by the runtime
and handed to what needs it (docs/ARCHITECTURE.md, "The layers, revisited"); this fails
the build on one built anywhere else. Tests build fakes, and the models' own classes
for the pieces they check.
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402

RUNTIME = os.path.join("tagpup", "runtime.py")
CLIP = os.path.join("tagpup", "ml", "clip.py")
FACES = os.path.join("tagpup", "ml", "faces.py")

#: What builds a model, and the one file each may be called in. The model classes are
#: the runtime's to build; what they are made of, their own module's. The old names
#: (the shims in scripts/ for the tests) are built nowhere that ships.
BUILDERS = {
    "ClipModel": RUNTIME,
    "FaceModel": RUNTIME,
    "create_model_and_transforms": CLIP,
    "MTCNN": FACES,
    "InceptionResnetV1": FACES,
    "ClipEmbedder": None,
    "FaceProcessor": None,
}


def calls(source):
    """(name, line) of each call whose callee is named -- `X(...)` or `a.X(...)`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name:
                yield name, node.lineno


def offenders(relative, source):
    return ["%s:%d builds %s" % (relative, line, name) for name, line in calls(source)
            if name in BUILDERS and BUILDERS[name] != relative]


class ModelsHaveOneOwner(unittest.TestCase):
    def test_only_the_runtime_builds_a_model(self):
        problems, checked = [], 0
        for relative in python_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                problems += offenders(relative, handle.read())
            checked += 1
        self.assertGreater(checked, 50, "the guard found nothing to check")
        self.assertEqual(problems, [], "\n\nBuild a model in tagpup.runtime and hand it on:\n\n" + "\n".join(problems))

    def test_the_guard_recognises_what_it_forbids(self):
        samples = [
            ("tagpup_cli.py", "processor = FaceProcessor()", True),
            ("tagpup_cli.py", "embedder = ClipEmbedder(photo_index=index, **settings)", True),
            (os.path.join("tagpup", "services", "suggester.py"), "model = clip.ClipModel(**settings)", True),
            (os.path.join("tagpup", "services", "suggester.py"), "faces = FaceModel(**settings)", True),
            (os.path.join("scripts", "faces.py"), "detector = MTCNN(keep_all=True)", True),
            (RUNTIME, "self._clip = ClipModel(**self.embedder_settings)", False),
            (RUNTIME, "self._faces = FaceModel(**self.face_settings)", False),
            (FACES, "self.mtcnn = MTCNN(keep_all=True)", False),
            (os.path.join("scripts", "faces.py"), "class FaceProcessor(face_models.FaceModel):\n    pass", False),
            ("tagpup_cli.py", "processor = runtime.faces", False),
        ]
        for relative, source, expected in samples:
            self.assertEqual(expected, bool(offenders(relative, source)), "%s: %s" % (relative, source))


if __name__ == "__main__":
    unittest.main()
