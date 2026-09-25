"""The file names Smart Rename gives photos: "<grouping> - <index> - <caption>" in the
default rename format (a library's renaming.format setting).

Smart Rename makes one for each photo it renames, and editing the caption of a photo
it named makes a new one. Both go through file_base; each had its own copy.
"""

#: What separates the parts of a Smart Rename name. Editing a photo's caption renames
#: it by splitting its name here, so a grouping may not hold one (the "grouping" kind
#: of tagpup.core.validation).
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

