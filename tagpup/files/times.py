"""Changing when a photo file says it was taken."""
import re

from tagpup.files.exiftool_session import ExifToolSession


def shift_date_taken(exiftool_path, photo_paths, minutes):
    """Move DateTimeOriginal and CreateDate in each photo by `minutes`.

    Returns how many files ExifTool reports it updated: its own count, so a photo it
    could not write is not counted.
    """
    sign = "+" if minutes >= 0 else "-"
    by = "0:0:0 0:%d:0" % abs(minutes)
    updated = 0
    # check_execute=False: a batch with one unwritable photo still shifts the rest,
    # and ExifTool's summary line says how many it did.
    with ExifToolSession(executable=exiftool_path, check_execute=False) as et:
        for i in range(0, len(photo_paths), 50):
            out = et.execute("-DateTimeOriginal%s=%s" % (sign, by), "-CreateDate%s=%s" % (sign, by),
                             "-overwrite_original", *photo_paths[i:i + 50])
            updated += sum(int(n) for n in re.findall(r"(\d+) image files? updated", out or ""))
    return updated
