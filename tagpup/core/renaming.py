"""The file names Smart Rename gives photos: "<grouping> - <index> - <caption>" in the
default format config.ini holds.
"""

#: What separates the parts of a Smart Rename name. Editing a photo's caption renames
#: it by splitting its name here, so a grouping may not hold one.
SEPARATOR = " - "


def problem_with_grouping(grouping):
    """Why a Smart Rename grouping cannot be used, or None if it can.

    A grouping holding " - " ("2019-06 - Summer Camp") put a separator before the
    photo's number, and editing a caption later took the grouping's second half for
    the number and dropped the real one (docs/findings.md #29). A dash without spaces,
    as in "2019-06", separates nothing. The pages ask the same (groupingProblem in
    gui_tagpup/app.js), held to tests/tag_rules.json.
    """
    if SEPARATOR in str(grouping or ""):
        return ('A grouping cannot contain " - ": it separates the parts of a photo\'s name. '
                'A dash without spaces, as in 2019-06, is fine.')
    return None
