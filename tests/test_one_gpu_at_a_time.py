"""One process at a time has a model on the graphics card (docs/findings.md, #750).

Adding a folder started its index (a child of the server) and Suggest (in the server) on
the same photos at once. Each loaded its own ViT-H-14 onto a 10 GB card, and both stayed
at 0 of 165 for over half an hour; nothing said why. tagpup.ml.gpu gives the card to one
process at a time, in order; a waiter says who has it and since when, stops at once when
cancelled, and proceeds when a holder ends however it ends. These tests take turns on a
folder of their own (TAGPUP_GPU_LOCK, or a Card made on it), never the owner's, and run
no real model: a stand-in says it is on "cuda".
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402

from tagpup.core import gpu_turns, processes  # noqa: E402
from tagpup.ml import gpu  # noqa: E402

#: A process that takes a turn as `what` on the folder it is given, saying each waiting
#: line, then holds it until the release file appears.
HOLDER = """
import os, sys, time
sys.path.insert(0, os.getcwd())
from tagpup.ml import gpu
where, what, release = sys.argv[1:4]
hold = gpu.Card(where, poll=0.05).hold(what, report=lambda line: print("WAIT " + line, flush=True))
print("HELD " + what, flush=True)
while not os.path.exists(release):
    time.sleep(0.05)
hold.release()
print("RELEASED " + what, flush=True)
"""

DEADLINE = 20

#: A test process that takes its turn on the default card -- in the folder a test run is
#: given, TAGPUP_GPU_LOCK unset -- says the folder, and ends without letting go.
LEAVES_IT_HELD = """
import os, sys
sys.path.insert(0, os.getcwd())
from tagpup.ml import gpu
gpu.card().hold("Suggest Regatta (harbour)")
print(gpu.folder(), flush=True)
"""


def until(condition, seconds=DEADLINE, step=0.02):
    """Wait for `condition()`; whether it came true."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(step)
    return condition()


class _Folder(unittest.TestCase):
    def setUp(self):
        self.where = tempfile.mkdtemp(prefix="tagpup_gpu_")
        self.children = []
        self.cards = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                processes.kill_tree(child.pid)
                child.wait(timeout=30)
            if child.stdout:
                child.stdout.close()
        # Every turn a test took ends here, so its folder goes with it (#775): an open
        # card.lock cannot be deleted, and a folder was left in %TEMP% for each.
        for card in self.cards:
            card.close()
        self.assertTrue(until(lambda: own_home.remove(self.where), 10), "left behind: %s" % self.where)

    def card(self, **kwargs):
        kwargs.setdefault("poll", 0.05)
        card = gpu.Card(self.where, **kwargs)
        self.cards.append(card)
        return card

    def holder(self, what):
        """A process holding the card as `what` until release(what) -- once it says so."""
        child = processes.start([sys.executable, "-c", HOLDER, self.where, what, self._release_file(what)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=WORKSPACE_DIR)
        self.children.append(child)
        return child

    def _release_file(self, what):
        return os.path.join(self.where, "release-" + what.replace(" ", "_"))

    def release(self, what):
        with open(self._release_file(what), "w") as handle:
            handle.write("go")

    @staticmethod
    def read_until(child, start):
        """The lines `child` printed up to and including the first that starts with `start`."""
        lines = []
        for line in iter(child.stdout.readline, ""):
            lines.append(line.strip())
            if line.startswith(start):
                return lines
        raise AssertionError("the process ended without saying %r: %s" % (start, lines))

    def take_in_thread(self, card, what, **kwargs):
        """Take `card` on a thread: (thread, outcome) -- outcome gets "hold" or "error"."""
        outcome = {}

        def take():
            try:
                outcome["hold"] = card.hold(what, **kwargs)
                outcome["at"] = time.monotonic()
            except BaseException as e:
                outcome["error"] = e
        thread = threading.Thread(target=take, daemon=True)
        thread.start()
        return thread, outcome


class TwoProcessesTakeTurns(_Folder):
    def test_the_second_waits_saying_who_has_it_then_runs(self):
        first = self.holder("indexing Regatta (harbour)")
        self.read_until(first, "HELD")
        second = self.holder("Suggest Regatta (harbour)")
        said = self.read_until(second, "WAIT")
        self.assertTrue(gpu_turns.is_waiting(said[-1][5:]), said)
        self.assertIn("in use by: indexing Regatta (harbour), since ", said[-1])
        # It is still waiting: the first has not let go.
        time.sleep(0.5)
        self.assertIsNone(second.poll())
        self.release("indexing Regatta (harbour)")
        self.assertEqual("HELD Suggest Regatta (harbour)", self.read_until(second, "HELD")[-1])
        self.assertEqual("RELEASED indexing Regatta (harbour)", self.read_until(first, "RELEASED")[-1])
        self.release("Suggest Regatta (harbour)")
        self.read_until(second, "RELEASED")

    def test_the_next_proceeds_when_the_holder_is_killed(self):
        first = self.holder("indexing Lighthouse (harbour)")
        self.read_until(first, "HELD")
        lines = []
        thread, outcome = self.take_in_thread(self.card(), "Suggest Lighthouse (harbour)", report=lines.append)
        self.assertTrue(until(lambda: lines), "the waiter never said it waits")
        self.assertIn("indexing Lighthouse (harbour)", lines[0])
        processes.kill_tree(first.pid)
        first.wait(timeout=30)
        thread.join(DEADLINE)
        self.assertIn("hold", outcome, outcome)
        outcome["hold"].release()


class AWaitThatIsCancelled(_Folder):
    def test_it_stops_at_once_and_leaves_no_ticket(self):
        holder = self.card()
        held = holder.hold("indexing Breakwater (harbour)")
        self.addCleanup(held.release)
        stop = threading.Event()
        lines = []
        thread, outcome = self.take_in_thread(self.card(), "Suggest Breakwater (harbour)",
                                              cancelled=stop.is_set, report=lines.append)
        self.assertTrue(until(lambda: lines))
        asked = time.monotonic()
        stop.set()
        thread.join(DEADLINE)
        self.assertIsInstance(outcome.get("error"), gpu_turns.Cancelled)
        self.assertLess(time.monotonic() - asked, 2.0)
        self.assertEqual([], os.listdir(os.path.join(self.where, gpu.QUEUE)))
        # The holder still has it, and the next waiter is behind no one.
        self.assertTrue(holder.holds())
        self.assertFalse(gpu.others_waiting(self.where))

    def test_a_dead_waiters_ticket_holds_no_one_up(self):
        os.makedirs(os.path.join(self.where, gpu.QUEUE))
        # A waiter killed while it waited leaves its ticket, which nothing holds now.
        with open(os.path.join(self.where, gpu.QUEUE, "%020d-1-1.ticket" % 1), "w"):
            pass
        thread, outcome = self.take_in_thread(self.card(), "indexing Pier (harbour)")
        thread.join(DEADLINE)
        self.assertIn("hold", outcome, outcome)
        outcome["hold"].release()


class ATestRunsFolder(unittest.TestCase):
    def test_is_deleted_when_the_process_ends_holding_the_card(self):
        env = {key: value for key, value in os.environ.items() if key != gpu.ENV}
        env["TAGPUP_NO_MODEL_WEIGHTS"] = "1"
        done = processes.run([sys.executable, "-c", LEAVES_IT_HELD], capture_output=True, text=True,
                             cwd=WORKSPACE_DIR, env=env, timeout=60)
        folder = done.stdout.strip()
        self.assertTrue(os.path.basename(folder).startswith("tagpup_gpu_test_"), done.stdout + done.stderr)
        self.assertFalse(os.path.exists(folder), "a test run's lock folder was left behind (#775)")


class InOrder(_Folder):
    def test_waiters_take_it_in_the_order_they_asked(self):
        held = self.card().hold("indexing Quay (harbour)")
        order = []
        first_card, second_card = self.card(), self.card()
        one, first = self.take_in_thread(first_card, "first")
        self.assertTrue(until(lambda: gpu.others_waiting(self.where)))
        two, second = self.take_in_thread(second_card, "second")
        self.assertTrue(until(lambda: len(os.listdir(os.path.join(self.where, gpu.QUEUE))) == 2))
        held.release()
        one.join(DEADLINE)
        self.assertIn("hold", first)
        order.append("first")
        time.sleep(0.3)
        self.assertNotIn("hold", second, "the second took the card while the first had it")
        first["hold"].release()
        two.join(DEADLINE)
        self.assertIn("hold", second)
        order.append("second")
        second["hold"].release()
        self.assertEqual(["first", "second"], order)

    def test_threads_of_one_process_share_its_turn(self):
        card = self.card()
        one = card.hold("Suggest Quay (harbour)")
        two = card.hold("Suggest Slipway (harbour)")
        self.assertIn("Suggest Slipway (harbour)", gpu.read_holder(self.where)["what"])
        one.release()
        self.assertTrue(card.holds())
        two.release()
        self.assertFalse(card.holds())
        took = self.card().try_hold("indexing Quay (harbour)")
        self.assertIsNotNone(took)
        took.release()


class KeptBetweenRuns(_Folder):
    def test_an_idle_holder_unloads_and_gives_way_to_a_waiter(self):
        unloaded = []
        keeper = self.card(yield_poll=0.05)
        keeper.keep_when_idle = True
        keeper.on_release = lambda: unloaded.append(True)
        keeper.hold("warming Suggest's models (TagPup server)").release()
        self.assertTrue(keeper.holds(), "the models were kept, and the turn with them")
        self.assertEqual([], unloaded)
        thread, outcome = self.take_in_thread(self.card(), "indexing Mooring (harbour)")
        thread.join(DEADLINE)
        self.assertIn("hold", outcome, outcome)
        self.assertEqual([True], unloaded, "the models were not let go before the card was")
        self.assertFalse(keeper.holds())
        outcome["hold"].release()

    def test_a_kept_turn_is_not_given_up_under_a_run(self):
        unloaded = []
        keeper = self.card(yield_poll=0.05)
        keeper.keep_when_idle = True
        keeper.on_release = lambda: unloaded.append(True)
        run = keeper.hold("Suggest Mooring (harbour)")
        thread, outcome = self.take_in_thread(self.card(), "indexing Mooring (harbour)")
        time.sleep(0.4)
        self.assertNotIn("hold", outcome)
        self.assertEqual([], unloaded)
        run.release()
        thread.join(DEADLINE)
        self.assertIn("hold", outcome)
        self.assertEqual([True], unloaded)
        outcome["hold"].release()

    def test_try_hold_refuses_while_another_holds_or_waits(self):
        other = self.card().hold("indexing Jetty (harbour)")
        self.assertIsNone(self.card().try_hold("warming"))
        waiter_card = self.card()
        thread, outcome = self.take_in_thread(waiter_card, "Suggest Jetty (harbour)")
        self.assertTrue(until(lambda: gpu.others_waiting(self.where)))
        other.release()
        thread.join(DEADLINE)
        outcome["hold"].release()
        took = self.card().try_hold("warming")
        self.assertIsNotNone(took)
        took.release()


class ModelsKeptForGood(_Folder):
    """--release-models-after 0: the web server keeps its models for good (#774). The turn on
    the card is kept with them, and they are unloaded only when another process waits."""

    def test_the_warm_ups_models_stay_loaded(self):
        from tagpup.core.library import Library
        from tagpup.runtime import Runtime
        from tagpup.services import libraries as library_actions
        home = own_home.for_test(self)
        library_actions.create(home.library("harbour.db"))
        built = []

        def build(settings):
            built.append(TheRuntimesTurns.Model())
            return built[-1]
        card = self.card(yield_poll=0.05)
        runtime = Runtime(build_clip=build, build_faces=build, idle_after=None, card=card, keep_models=True)
        runtime.warm_up([Library(home.library("harbour.db"))])
        time.sleep(0.3)
        self.assertEqual([0, 0], [model.unloads for model in built], "the warm-up's models were let go")
        self.assertTrue(card.holds())
        self.assertFalse(runtime.release_idle(), "with no idle period nothing is let go")
        self.assertEqual([0, 0], [model.unloads for model in built])
        card.release_if_idle()
        self.assertFalse(card.holds())

    def test_the_cli_lets_go_as_its_run_ends(self):
        from tagpup.runtime import Runtime
        card = self.card()
        clip = TheRuntimesTurns.Model()
        runtime = Runtime(clip=clip, card=card)
        runtime.gpu_turn("indexing Regatta (harbour)", (clip,)).release()
        self.assertFalse(card.holds())
        self.assertEqual(1, clip.unloads)


class TheRuntimesTurns(_Folder):
    """What the runtime does with the card: a run's turn is taken when a model is used, a
    model off the card takes none, and the warm-up never makes anyone wait."""

    class Model:
        def __init__(self, device="cuda"):
            self.device = device
            self.loads = self.unloads = self.used = 0

        def load(self):
            self.loads += 1

        def unload(self):
            self.unloads += 1

        def embed_text(self, text):
            self.used += 1
            return [0.0]

    def test_a_run_takes_its_turn_when_it_first_uses_a_model(self):
        from tagpup import runtime as runtimes
        card, clip = self.card(), self.Model()
        turn = runtimes._RunTurn(card, "Suggest Harbour Wall (harbour)", (clip,))
        proxy = runtimes._OnTheCard(clip, turn)
        self.assertFalse(card.holds(), "a run that has used no model has no turn")
        self.assertEqual("cuda", proxy.device)
        proxy.embed_text("a regatta")
        self.assertTrue(card.holds())
        self.assertEqual(1, clip.used)
        turn.close()
        self.assertFalse(card.holds())

    def test_a_model_off_the_card_waits_for_no_one(self):
        from tagpup import runtime as runtimes
        other = self.card().hold("indexing Harbour Wall (harbour)")
        self.addCleanup(other.release)
        clip = self.Model(device="cpu")
        turn = runtimes._RunTurn(self.card(), "Suggest Harbour Wall (harbour)", (clip,))
        runtimes._OnTheCard(clip, turn).embed_text("a regatta")
        self.assertEqual(1, clip.used)

    def test_the_warm_up_is_skipped_while_another_has_the_card(self):
        from tagpup.core.library import Library
        from tagpup.runtime import Runtime
        from tagpup.services import libraries as library_actions
        home = own_home.for_test(self)
        library_actions.create(home.library("harbour.db"))
        built = []

        def build(settings):
            built.append(self.Model())
            return built[-1]
        card = self.card(yield_poll=0.05)
        runtime = Runtime(build_clip=build, build_faces=build, idle_after=60, card=card, keep_models=True)
        other = self.card().hold("indexing Breakwater (harbour)")
        runtime.warm_up([Library(home.library("harbour.db"))])
        self.assertEqual([0, 0], [model.loads + model.used for model in built], "it loaded with the card taken")
        other.release()

        runtime.warm_up([Library(home.library("harbour.db"))])
        self.assertEqual(1, built[0].used)
        self.assertEqual(1, built[1].loads)
        self.assertTrue(card.holds(), "the web server keeps its turn while its models are loaded")
        # An index arrives: the server lets its models go, and the index has the card.
        thread, outcome = self.take_in_thread(self.card(), "indexing Breakwater (harbour)")
        thread.join(DEADLINE)
        self.assertIn("hold", outcome)
        self.assertEqual([1, 1], [model.unloads for model in built])
        outcome["hold"].release()


if __name__ == "__main__":
    unittest.main()
