"""The tag vocabulary: how a tag path is spelled and taken apart.

A tag is a path of segments -- Family/Immediate/Rowan Thackeray -- written with "/";
"|" and "\\" are read as the same separator on the way in, as some tools write them. A
person is the leaf of a tag under a people root. The pages take tags apart with
leafOf / rootOf / samePerson (gui_tagpup/app.js), which split on "/" alone.

Python took tags apart by hand in 55 places, not all alike: some trimmed segments, some
dropped empty ones, a few read "|" as a separator and most did not. Reading every tag
this one way changed the meaning of none in either library: on 2026-09-23 the only tag
holding a "|" was a test tag on one photo, since removed. tests/test_vocabulary.py fails
the build on a raw split of a tag anywhere else.
"""

SEPARATOR = "/"


def normalize(tag):
    """The one spelling of a tag: "/" between segments, each trimmed, none empty."""
    return SEPARATOR.join(segments(tag))


def segments(tag):
    """A tag's segments, trimmed, empty ones dropped: " A | B/ C" -> ["A", "B", "C"]."""
    if not tag:
        return []
    text = str(tag).replace("|", SEPARATOR).replace("\\", SEPARATOR)  # not a path: tag separators
    return [part.strip() for part in text.split(SEPARATOR) if part.strip()]


def leaf_of(tag):
    """The last segment -- the name, for a person: "People/Rowan Thackeray" -> "Rowan Thackeray"."""
    parts = segments(tag)
    return parts[-1] if parts else ""


def root_of(tag):
    """The first segment: "People/Rowan Thackeray" -> "People"."""
    parts = segments(tag)
    return parts[0] if parts else ""


def parent_of(tag):
    """The path above the leaf, or "" for a root: "A/B/C" -> "A/B"."""
    return SEPARATOR.join(segments(tag)[:-1])


def lineage(tag):
    """Every path from the root down to the tag itself: "A/B/C" -> ["A", "A/B", "A/B/C"]."""
    parts = segments(tag)
    return [SEPARATOR.join(parts[:i]) for i in range(1, len(parts) + 1)]


def with_leaf(tag, leaf):
    """The same path with another leaf: ("Family/Rowan", "Rowan T") -> "Family/Rowan T"."""
    return SEPARATOR.join(segments(tag)[:-1] + [str(leaf).strip()])


def key(text):
    """The form two names or segments are compared in: trimmed, case ignored."""
    return str(text or "").strip().lower()


def same_person(a, b):
    """Do two tags or names name the same person? Their leaves, compared without case."""
    left = key(leaf_of(a))
    return bool(left) and left == key(leaf_of(b))


def hidden_by(tag, hidden):
    """Is the tag, or any path above it, in `hidden`? (A hidden branch hides its leaves.)"""
    return any(path in hidden for path in lineage(tag))
