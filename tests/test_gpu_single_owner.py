"""The graphics card has one owner, tagpup.ml.gpu, and a model is loaded onto it only with
the process's turn (docs/findings.md, #750).

The server's Suggest and the indexer it had started each loaded a ViT-H-14 onto one 10 GB
card at once; both sat at 0 of 165 for over half an hour, and nothing said why. A turn is
a lock every TagPup process on the machine shares, taken by the runtime for a run
(tagpup.runtime) and by each model before it loads on the card (its own check). This
fails the build on a model module that loads weights without that check, on a turn taken
anywhere but the runtime, and on a lock of the card made anywhere but its owner.
"""
import ast
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shipped_sources import ROOT, python_sources  # noqa: E402
from test_models_single_owner import calls  # noqa: E402

OWNER = os.path.join("tagpup", "ml", "gpu.py")
RUNTIME = os.path.join("tagpup", "runtime.py")
#: Where a turn may be taken: the owner, the runtime that hands models their turn, and
#: each model's own check before it loads.
TAKERS = {OWNER, RUNTIME, os.path.join("tagpup", "ml", "clip.py"), os.path.join("tagpup", "ml", "faces.py")}
#: The byte locks of the machine's shared files: the card's, and the installer's own
#: (tagpup.supervisor.Lock), which is not the card's.
LOCKERS = {OWNER, os.path.join("tagpup", "supervisor.py")}

#: What loads a model's weights (onto its device): open_clip's and facenet's own.
WEIGHTS = {"create_model_and_transforms", "MTCNN", "InceptionResnetV1"}

TURNS = re.compile(r"\b(gpu|card)\b.*\.(hold|try_hold|ensure|release_if_idle)\(|\bgpu\.Card\(")
LOCKS = re.compile(r"msvcrt\.locking|fcntl\.flock|\bcard\.lock\b|TAGPUP_GPU_LOCK")


def loads_weights(source):
    """Does the module call one of the builders of a model's weights?"""
    return any(name in WEIGHTS for name, _line in calls(source))


def checks_the_card(source):
    """(class, function) of each function that asks gpu.on_the_card and takes the turn
    (`.ensure(`) before it loads."""
    found = set()
    tree = ast.parse(source)
    for cls in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
        for function in [node for node in cls.body if isinstance(node, ast.FunctionDef)]:
            names = {name for name, _line in calls(ast.unparse(function))}
            if "on_the_card" in names and "ensure" in names:
                found.add((cls.name, function.name))
    return found


def lines_matching(pattern, relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return ["%s:%d" % (relative, number) for number, line in enumerate(handle, 1)
                if pattern.search(line) and not line.lstrip().startswith("#")]


class TheCardHasOneOwner(unittest.TestCase):
    def test_a_module_that_loads_weights_takes_the_turn_first(self):
        problems, checked = [], 0
        for relative in python_sources():
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                source = handle.read()
            if not loads_weights(source):
                continue
            checked += 1
            if not checks_the_card(source):
                problems.append("%s loads a model's weights with no gpu.on_the_card / ensure before it" % relative)
        self.assertGreaterEqual(checked, 2, "the guard found no module that loads weights")
        self.assertEqual([], problems, "\n\nTake the turn on the graphics card (tagpup.ml.gpu) before loading:\n\n"
                         + "\n".join(problems))

    def test_turns_are_taken_only_by_the_runtime(self):
        problems = []
        for relative in python_sources():
            if relative not in TAKERS:
                problems += lines_matching(TURNS, relative)
        self.assertEqual([], problems, "\n\nTake a turn through tagpup.runtime (gpu_turn, begin):\n\n"
                         + "\n".join(problems))

    def test_the_card_is_locked_only_by_its_owner(self):
        problems = []
        for relative in python_sources():
            if relative not in LOCKERS:
                problems += lines_matching(LOCKS, relative)
        self.assertEqual([], problems)

    def test_the_guard_recognises_what_it_forbids(self):
        unchecked = ("from facenet_pytorch import MTCNN\n"
                     "class Faces:\n"
                     "    def load(self):\n"
                     "        self.mtcnn = MTCNN(keep_all=True)\n")
        checked = ("class Faces:\n"
                   "    def _init(self):\n"
                   "        if gpu.on_the_card(self.device):\n"
                   "            (self.gpu or gpu.card()).ensure('loading the face models')\n"
                   "        self.mtcnn = MTCNN(keep_all=True)\n")
        self.assertTrue(loads_weights(unchecked))
        self.assertFalse(checks_the_card(unchecked))
        self.assertTrue(checks_the_card(checked))
        self.assertTrue(TURNS.search("hold = gpu.card().hold('indexing')"))
        self.assertTrue(TURNS.search("card = gpu.Card(where)"))
        self.assertFalse(TURNS.search("self.card = card if card is not None else gpu.card()"))
        self.assertTrue(LOCKS.search("msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)"))


if __name__ == "__main__":
    unittest.main()
