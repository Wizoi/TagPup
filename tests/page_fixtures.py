"""The replies a page's test answers from (tests/frontend/fixtures/*.json), made by the real server in a Python test and
held to what that test makes now. The one rule of those files: a reply that differs from the file fails, naming the
command that writes it again; the file is written only when that command asks (TAGPUP_WRITE_FIXTURE=1), so a stale
fixture never passes by being rewritten (#2520; it was spelled in each test, and two of them rewrote their file).
"""
import os

WRITE_FIXTURE = "TAGPUP_WRITE_FIXTURE"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "fixtures")


def path_of(name):
    """The fixture `name` ("save_replies.json") of the page tests."""
    return os.path.join(FIXTURES, name)


def hold_to(test, fixture, text, module, what):
    """Fail `test` unless the file `fixture` holds `text`, the replies `what` ("the Activity page") reads as the test
    module `module` (tests.test_...) made them now; under TAGPUP_WRITE_FIXTURE=1 write it instead."""
    before = None
    if os.path.exists(fixture):
        with open(fixture, encoding="utf-8") as handle:
            before = handle.read()
    if before == text:
        return
    if os.environ.get(WRITE_FIXTURE) == "1":
        os.makedirs(os.path.dirname(fixture), exist_ok=True)
        with open(fixture, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return
    shown = os.path.relpath(fixture, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).replace(os.sep, "/")
    test.fail("The replies %s reads differ from %s, which its page test answers from. If the change is meant, write it "
              "again: %s=1 .venv/Scripts/python.exe -m unittest %s (then read the diff of %s)"
              % (what, shown, WRITE_FIXTURE, module, shown))
