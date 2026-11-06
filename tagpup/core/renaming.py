"""The file names Smart Rename gives photos: "<grouping> - <index> - <caption>" in the
default rename format (a library's renaming.format setting).

Smart Rename makes one for each photo it renames, and editing the caption of a photo
it named makes a new one. Both go through file_base; each had its own copy. parts_of reads
the grouping and the number back out of a name the format made.
"""
import re

#: What separates the parts of a Smart Rename name, so a grouping may not hold one (the
#: "grouping" kind of tagpup.core.validation): parts_of reads a name back by the format.
SEPARATOR = " - "

#: Characters a Windows file name may not hold.
NOT_IN_A_NAME = '<>:"/\\|?*'  # not a path: characters a name may not hold


def sanitize_filename(name):
    """Removes or replaces invalid filesystem characters to make the filename safe."""
    for c in NOT_IN_A_NAME:
        name = name.replace(c, '_')
    # Filter printable characters and strip
    name = "".join(ch for ch in name if ch.isprintable())
    return name.strip()


def file_base(fmt, grouping, index, caption):
    """The file name, without its extension, that the format `fmt` gives a photo.

    Without a caption the format's caption part is left out, separator and all.
    """
    caption = str(caption).strip()
    base = fmt.replace("{grouping}", grouping).replace("{index}", index)
    if caption:
        base = base.replace("{caption}", caption)
    else:
        base = base.replace(" - {caption}", "").replace("- {caption}", "").replace("{caption}", "")
    return sanitize_filename(base)


def parts_of(fmt, base):
    """(grouping, number) of `base`, a file name without its extension, when the format `fmt` made it; None when it
    holds no number where the format puts one (a camera's "IMG_0412", a name given by hand). The number is the text
    of the name, its zeros kept ("07"). Read with the caption and without it, as file_base makes both. The grouping is
    the shortest that is followed by a number: a name made before a grouping could not hold " - " (findings #29;
    "2019-06 - Summer Camp - 03 - Start", 10,223 of photo_index's photos, counted 2026-11-05) gives "2019-06 - Summer
    Camp" and 03, which Smart Rename then refuses as a grouping until it is retyped. The one reader of a Smart Rename
    name: Smart Rename of one photo and a caption edit's rename (metadata.title_filename) both read it here."""
    for made in (fmt, fmt.replace(" - {caption}", "").replace("- {caption}", "").replace("{caption}", "")):
        pattern = re.escape(made)
        for field, wanted in (("grouping", "(?P<grouping>.+?)"), ("index", "(?P<index>[0-9]+)"),
                              ("caption", "(?P<caption>.*)")):
            pattern = pattern.replace(re.escape("{%s}" % field), wanted)
        found = re.fullmatch(pattern, base)
        if found and "index" in found.groupdict():
            grouping = found.groupdict().get("grouping") or ""
            return grouping.strip(), found.group("index")
    return None

