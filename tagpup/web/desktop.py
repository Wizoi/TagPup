"""What the apps do on the desktop of the machine the server runs on: the folder dialog,
showing a photo in Explorer, opening one in its own program.

These stay in the web layer rather than becoming services: they act on this machine's
desktop and write nothing (docs/ARCHITECTURE.md, decisions, 2026-09-24). Both pages
have a Browse button, so the dialog is here for both blueprints.
"""
import os
import subprocess
import sys

from tagpup.core import paths
from tagpup.core import processes
from tagpup.services import photos as photo_actions

_FOLDER_DIALOG = (
    "import tkinter as tk; "
    "from tkinter import filedialog; "
    "root = tk.Tk(); "
    "root.withdraw(); "
    "root.lift(); "
    "root.focus_force(); "
    "root.attributes('-topmost', True); "
    "print(filedialog.askdirectory(title='Select Image Folder'))"
)


def ask_for_folder():
    """The folder picked in a folder dialog on this machine's desktop, or "" if it was
    cancelled. Browse, in either app.

    The dialog runs in an interpreter of its own, so no window or Tk state lives in the
    server.
    """
    picked = processes.run([sys.executable, "-c", _FOLDER_DIALOG], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
    return picked.stdout.strip()


def explorer_select_command(photo_path):
    """The command line that opens Explorer with this photo selected.

    Explorer parses its own command line and wants the path quoted after the
    switch -- /select,"D:\\a b\\c.jpg" -- not the whole switch quoted, which is what
    passing ["explorer.exe", "/select,<path>"] produced for any path with a space.
    A Windows path cannot contain a quote, so quoting it is safe.
    """
    return 'explorer.exe /select,"%s"' % paths.stored(photo_path)


def show_in_explorer(photo_path):
    """Open Explorer with the photo selected. Never through a shell, so nothing in the
    command is interpreted."""
    processes.start(explorer_select_command(photo_path))


def is_openable(photo_path):
    """May `photo_path` be opened in its own program? Only an existing file with a photo
    extension: startfile runs whatever it is handed, and this route only ever opens a
    photo."""
    return bool(photo_path) and os.path.isfile(photo_path) and photo_actions.is_photo(photo_path)


def open_photo(photo_path):
    """Open a photo in the application Windows associates with its type."""
    os.startfile(photo_path)
