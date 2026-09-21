"""Starting an app opens one browser tab, not two.

The reloader runs two processes: a parent supervisor that respawns, and a child that
holds the server. The browser guard was on `<APP>_RELOADED` alone, which is unset in
both of them on a first start -- so the supervisor opened a tab and the child opened
another.

It looked correct after a reload, because the parent sets `<APP>_RELOADED` on every
child it respawns. That is why it only ever showed at startup, and why it survived.

`_CHILD` means "I am the process with the server". `_RELOADED` means "this is a
restart, they already have a tab open". Both questions have to be asked.
"""
import os
import sys
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)

LAUNCHERS = {"tagpup_gui.py": "TAGPUP", "tagtuner.py": "TAGTUNER"}


def guard_of(filename):
    """The condition guarding the browser-opening block."""
    with open(os.path.join(WORKSPACE_DIR, filename), encoding="utf-8") as f:
        text = f.read()
    opener = text.index("webbrowser.open")
    # The nearest `if` above it that is not a comment.
    before = text[:opener].split("\n")
    for line in reversed(before):
        stripped = line.strip()
        if stripped.startswith("if ") and stripped.endswith(":"):
            return stripped, text
    raise AssertionError("no guard found above webbrowser.open in %s" % filename)


class TestOneTab(unittest.TestCase):
    def test_each_launcher_asks_whether_it_is_the_server(self):
        for filename, prefix in LAUNCHERS.items():
            guard, text = guard_of(filename)
            self.assertIn(
                "%s_RELOADED_CHILD" % prefix, text,
                "%s does not distinguish the supervisor from the server, so both "
                "open a tab" % filename)
            self.assertIn("serving", guard, filename)

    def test_each_launcher_asks_whether_this_is_a_restart(self):
        for filename, _prefix in LAUNCHERS.items():
            guard, _text = guard_of(filename)
            self.assertIn(
                "restarting", guard,
                "%s would open a new tab on every auto-reload" % filename)

    def test_the_guard_needs_both_answers(self):
        for filename, _prefix in LAUNCHERS.items():
            guard, _text = guard_of(filename)
            self.assertRegex(
                guard, r"if serving and not restarting:",
                "%s guards the browser on one condition; two processes and two "
                "reasons need two" % filename)

    def test_the_conditions_are_read_from_the_environment(self):
        # The supervisor passes them to the child as environment variables; reading
        # them from anywhere else would be reading something the parent cannot set.
        for filename, prefix in LAUNCHERS.items():
            _guard, text = guard_of(filename)
            self.assertRegex(
                text, r'serving = bool\(os\.environ\.get\("%s_RELOADED_CHILD"\)\)' % prefix)
            self.assertRegex(
                text, r'restarting = bool\(os\.environ\.get\("%s_RELOADED"\)\)' % prefix)


class TestTheReloaderStillSetsThem(unittest.TestCase):
    """The guard above is only meaningful if the reloader still sets what it reads."""

    def test_the_child_is_marked_as_the_child(self):
        with open(os.path.join(WORKSPACE_DIR, "scripts", "reloader.py"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn('child_env_var = env_var_name + "_CHILD"', text)
        self.assertIn('child_env[child_env_var] = "1"', text)

    def test_a_respawn_is_marked_as_a_reload(self):
        with open(os.path.join(WORKSPACE_DIR, "scripts", "reloader.py"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, r'child_env\[env_var_name\] = "1"')


if __name__ == "__main__":
    unittest.main()
