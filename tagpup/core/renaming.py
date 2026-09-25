"""The file names Smart Rename gives photos: "<grouping> - <index> - <caption>" in the
default format config.ini holds.

Smart Rename makes one for each photo it renames, and editing the caption of a photo
it named makes a new one. Both go through file_base; each had its own copy.
"""

#: What separates the parts of a Smart Rename name. Editing a photo's caption renames
#: it by splitting its name here, so a grouping may not hold one.
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


def problem_with_grouping(grouping):
    """Why a Smart Rename grouping cannot be used, or None if it can.

    A grouping holding " - " ("2019-06 - Summer Camp") put a separator before the
    photo's number, and editing a caption later took the grouping's second half for
    the number and dropped the real one (docs/findings.md #29). A dash without spaces,
    as in "2019-06", separates nothing. The pages ask the same (groupingProblem in
    web/tagpup/main.js), held to tests/tag_rules.json.
    """
    if SEPARATOR in str(grouping or ""):
        return ('A grouping cannot contain " - ": it separates the parts of a photo\'s name. '
                'A dash without spaces, as in 2019-06, is fine.')
    return None
