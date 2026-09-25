"""The tag vocabulary: how a tag path is spelled and taken apart, and what a photo's
metadata says -- its tags, the people it names, its captions.

A tag is a path of segments -- Family/Immediate/Rowan Thackeray -- written with "/";
"|" and "\\" are read as the same separator on the way in, as some tools write them. A
person is the leaf of a tag under a people root. The pages take tags apart with
leafOf / rootOf / samePerson (web/common/vocabulary.js), which split on "/" alone.

Python took tags apart by hand in 55 places, not all alike: some trimmed segments, some
dropped empty ones, a few read "|" as a separator and most did not. Reading every tag
this one way changed the meaning of none in either library: on 2026-09-23 the only tag
holding a "|" was a test tag on one photo, since removed. tests/test_vocabulary.py fails
the build on a raw split of a tag anywhere else.

Reading the metadata out of a file is tagpup.files.metadata's; what a library's tag
tree says about people is read by tagpup.store.taxonomy and passed in. Everything here
works on what they hand over, and touches neither.
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


#: The face root a new library is given (tagpup.store.taxonomy.seed), and all that a
#: photo read without its library can assume. A library says which of its roots hold
#: faces in its tree, by has_face; no list of names here overrides it. There were five,
#: and they disagreed with the tree and with each other (docs/findings.md, #66).
NEW_LIBRARY_FACE_ROOT = "People"

#: The root a photo's activities are filed under: the caption puts them after its people.
ACTIVITY_ROOT = "Activity"

#: The roots of where a photo was taken, in the order a caption names them.
PLACE_ROOTS = ("School", "Trips")

#: The roots a new library is given beside its face root (tagpup.store.taxonomy.seed).
#: The seed, the caption (tagpup.core.suggesting) and the suggester each named these
#: themselves (docs/findings.md, #74).
NEW_LIBRARY_ROOTS = (ACTIVITY_ROOT, "Pets") + PLACE_ROOTS

#: Roots whose tags describe a photo's setting: the suggester marks such a tag down in a
#: folder where few photos share it.
CONTEXT_ROOTS = (ACTIVITY_ROOT,) + PLACE_ROOTS + ("Scenic", "Location", "Albums")


#: TagTuner's lists of faces that are not a person's, named where its people are listed:
#: the server names them from here, and its page keeps a copy a test holds to this.
BUCKETS = {"unknown": "Unknown Faces", "ungrouped": "Ungrouped", "excluded": "Excluded"}

#: What TagTuner shows on a face nobody is named on.
UNMATCHED = "Unmatched"

#: Names nobody can be given: a person called one opened that bucket instead of
#: themselves (docs/findings.md, #68). Whatever the case. tagpup.core.validation holds
#: a name to it.
NOT_A_PERSON = frozenset(name.lower() for name in (*BUCKETS.values(), UNMATCHED))


# What a photo's metadata says. `meta` is the record tagpup.files.metadata reads: every
# field under its ExifTool name ("XMP:Subject") and again bare ("Subject").

#: Keywords, flat and hierarchical.
KEYWORD_FIELDS = ("XMP:Subject", "Subject", "IPTC:Keywords", "Keywords")
HIERARCHY_FIELDS = ("XMP:HierarchicalSubject", "HierarchicalSubject")

#: People named outright, rather than by a keyword under a people root.
PERSON_FIELDS = ("XMP:PersonInImage", "PersonInImage", "XMP:RegionName", "RegionName")

#: Captions and titles, most wanted first: the first is the one shown.
CAPTION_FIELDS = ("IPTC:Caption-Abstract", "Caption-Abstract", "XMP:Description", "Description",
                  "XMP:Title", "Title", "IPTC:ObjectName", "ObjectName")


def _values(meta, fields):
    """Every value these fields hold, trimmed, in field order; empty ones left out."""
    found = []
    for key in fields:
        val = meta.get(key)
        if val:
            if isinstance(val, list):
                found.extend(str(v).strip() for v in val if v)
            else:
                found.append(str(val).strip())
    return found


def extract_tags(meta):
    """A photo's tags: every keyword once, in order.

    A flat keyword that is only a level of a hierarchical one on the same photo
    ("Family" beside "Family/Immediate/Cora Ingersoll") is left out: it is the path
    written again in pieces, not a tag of its own.
    """
    tags = list(dict.fromkeys(t for t in _values(meta, KEYWORD_FIELDS + HIERARCHY_FIELDS) if t))
    levels = {part for tag in tags if "/" in tag for part in segments(tag)}
    return [tag for tag in tags if "/" in tag or tag not in levels]


def extract_captions(meta):
    """A photo's captions and titles -- each distinct text once, in order.

    The reader records every field twice, prefixed and bare, and the same title is
    usually written to several fields, so reading them all listed each caption two or
    more times: 99.6% of indexed rows carried a duplicate. The first caption is the
    one everything shows.
    """
    return list(dict.fromkeys(c for c in _values(meta, CAPTION_FIELDS) if c))


class PeopleVocabulary:
    """What a library's tag tree says about people.

    `roots` are the lowercase face roots (People, Family, Pets, ...): a keyword under
    one names a person by its leaf. `by_keyword` maps a keyword, spelled as a full tag
    or as a bare leaf, to the person it names. tagpup.store.taxonomy reads one from a
    library. extract_people used to read both from the database for every photo and
    scan the whole tree per keyword -- 30s over 68,000 photos -- so anything resolving
    many photos reads this once and passes it.
    """

    #: What a photo read without its library assumes (NEW_LIBRARY_FACE_ROOT).
    DEFAULT_ROOTS = frozenset({NEW_LIBRARY_FACE_ROOT.lower()})

    def __init__(self, roots, by_keyword):
        self.roots = set(roots)
        self.by_keyword = by_keyword

    @classmethod
    def defaults(cls):
        """A new library's face root and nobody by name: a photo read without its library."""
        return cls(cls.DEFAULT_ROOTS, {})

    @classmethod
    def from_rows(cls, root_names, face_rows):
        """From a tag tree: the names of its face roots, and (tag, name) of every face node.
        The tree's roots only: a root it does not flag holds no faces."""
        roots = {name.lower().strip() for name in root_names if name}
        by_keyword = {}
        for tag, name in face_rows:
            # A face ROOT (People, Family, Pets, ...) is a category, not a person, so
            # a photo tagged plainly "Family" must not gain a name.
            if name and name.lower() in roots and "/" not in tag:
                continue
            # The first row to match a keyword by either spelling wins, as it did
            # when this was a scan in row order.
            if tag:
                by_keyword.setdefault(tag.lower(), name)
            if name:
                by_keyword.setdefault(name.lower(), name)
        return cls(roots, by_keyword)


def extract_people(meta, tags, known=None):
    """Whom a photo's metadata names: its person fields, and its keywords.

    A keyword names a person when it sits under one of the face roots in `known` (a
    PeopleVocabulary; without one, the usual roots), or when `known` has it as a face
    node of its own -- "Cora Ingersoll" with no hierarchy, filed as a person.
    """
    known = known or PeopleVocabulary.defaults()
    people = _values(meta, PERSON_FIELDS)
    for tag in tags:
        parts = segments(tag)
        if len(parts) >= 2 and parts[0].lower() in known.roots:
            people.append(parts[-1])
    for tag in tags:
        name = known.by_keyword.get(tag.replace("\\", "/").strip().lower())  # not a path: a keyword hierarchy
        if name:
            people.append(name)
    return list(dict.fromkeys(p for p in people if p))


def people_in_photo(meta, tags, face_names, known=None):
    """Everyone in a photo: whom its metadata names, and whom its faces were named as.

    What photos.people means. It has two sources and two kinds of writer: naming a
    face in TagTuner adds the person without necessarily writing a keyword, while
    every keyword write rebuilt the column from the keywords alone -- so tagging a
    photo silently took off everyone identified only by their face. Every writer of
    the column goes through here, so both sources always count.
    """
    people = extract_people(meta, tags, known)
    seen = {p.lower() for p in people}
    for name in face_names:
        if name.lower() not in seen:
            seen.add(name.lower())
            people.append(name)
    return people


def retag(tags, old, new=None):
    """`tags` with the tag `old` -- and every tag under it -- renamed to `new`, or taken
    off without one. Returns (the tags, whether any changed).

    "People/Rowan" renamed to "Family/Rowan" takes "People/Rowan/Swim Team" with it, to
    "Family/Rowan/Swim Team"; "People/Rowanne" is another tag and is left alone.
    """
    result, changed = [], False
    for tag in tags:
        spelled = normalize(tag)
        if spelled == old or spelled.startswith(old + SEPARATOR):
            changed = True
            if new:
                result.append(new + spelled[len(old):])
        else:
            result.append(tag)
    return result, changed


def resolve_people(tags, people_paths):
    """Give every person in `tags` the path they are filed under.

    `people_paths` maps a lowercased name to its tag (tagpup.store.taxonomy.people_paths).
    This is the last line of defence, and deliberately at the write boundary rather
    than at each caller. A person's name reaches this program as a leaf from half a
    dozen directions -- the faces table, CLIP suggestions, neighbour propagation, a
    typed name -- and each of those resolving it for itself is exactly how "Hailey
    Brookmire" kept being written beside "People/Hazel Brookmire". One of them always
    gets missed; folder auto-apply was the one that outlived three fixes.

    Only a bare tag whose name matches somebody already in the people tree is touched.
    A flat keyword that is not a person -- "Cross Country", "Kentridge" -- is legitimate
    and is left exactly as it is. A leaf duplicating a path already on the photo is
    dropped.
    """
    if not people_paths:
        return list(tags)

    pathed_leaves = {key(leaf_of(t)) for t in tags if "/" in t}

    resolved = []
    for tag in tags:
        if "/" in tag:
            if tag not in resolved:
                resolved.append(tag)
            continue
        low = str(tag).strip().lower()
        if low in pathed_leaves:
            continue
        person = people_paths.get(low)
        target = person or tag
        if target not in resolved:
            resolved.append(target)
    return resolved
