"""Two threads opening the same library's state at once share one copy of it.

Each server kept its per-library state -- saved suggestions, folder caches, index
queues -- in a registry keyed by library, and made a library's entry with
"if missing: registry[key] = {}". Two request threads reaching that line together
each made one, and whatever the first had put in its copy was lost.

The state is a tagpup.core.per_library.PerLibrary now, which makes a library's value under
a lock. A race cannot be timed in a test, so the first thread's make is held open
while the second asks, which is what the second thread saw.
"""
import threading
import unittest

from tagpup.core.library import Library
from tagpup.core.per_library import PerLibrary


class LibraryRegistryRace(unittest.TestCase):
    def test_the_second_thread_gets_the_first_threads_value(self):
        library = Library("libraries/regatta.db")
        making = threading.Event()
        release = threading.Event()
        made = []

        def make(library):
            made.append({"folder": "state"})
            making.set()
            self.assertTrue(release.wait(5), "the test never let the first make finish")
            return made[-1]

        state = PerLibrary(make)
        first = []
        thread = threading.Thread(target=lambda: first.append(state.of(library)))
        thread.start()
        self.assertTrue(making.wait(5), "the first thread never started making")

        second = []
        other = threading.Thread(target=lambda: second.append(state.of(library)))
        other.start()
        other.join(0.2)
        self.assertTrue(other.is_alive(), "the second thread made a copy of its own instead of waiting")

        release.set()
        thread.join(5)
        other.join(5)
        self.assertEqual(len(made), 1, "the other thread's entry was replaced")
        self.assertIs(first[0], second[0])
        self.assertEqual("state", second[0].get("folder"))

    def test_the_same_library_under_another_spelling_is_one_entry(self):
        state = PerLibrary(lambda library: object())
        self.assertIs(state.of(Library("libraries/regatta.db")),
                      state.of(Library("LIBRARIES\\Regatta.db")))


if __name__ == "__main__":
    unittest.main()
