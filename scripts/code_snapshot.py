"""What the code is, for anything that copies it: the measurement sandbox and the installer.

Each kept its own list of folders once, and the sandbox's lacked tagpup/ when the
foundation modules moved there: its server could not start. One list here, and
tests/test_sandbox_has_all_the_code.py makes both apps from a copy made with it.
"""
import os
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Folders the servers import from or serve.
FOLDERS = ("scripts", "tagpup", "gui", "gui_tagpup")

#: The programs people start, at the top of the repository.
LAUNCHERS = ("tagpup_web.py", "tagpup_cli.py", "runner.py")


def copy_code(destination, code_root=REPO_ROOT, launchers=False):
    """Copy the code into `destination`: the folders, and the launchers if asked."""
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in FOLDERS:
        source = os.path.join(code_root, name)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(destination, name), ignore=ignore)
    if launchers:
        for name in LAUNCHERS:
            shutil.copy2(os.path.join(code_root, name), os.path.join(destination, name))
