"""The one-time move of clips' Windows Tags into their XMP (DECISIONS 2026-11-09; scripts/move_windows_tags.py).

A clip tagged long ago in Windows Photo Gallery holds its Tags in the WM/Category entry of its moov/udta/Xtra box
(fields.CLIP_CATEGORY), which TagPup never reads as tags (DECISIONS 2026-11-07). The move writes them into the clip's XMP
tags, beside the tags it holds, and frees the Xtra in place in the same patch (tagpup.files.clip_patch); History's Undo
gives the Xtra back. One time only: nothing reads Category after it, and a clip tagged in Windows afterwards is not picked
up (the owner's answer, 2026-11-09).

One planner (planner) for the survey (the dry run) and the move (--apply): a clip holding no Windows Tags is left alone;
one holding a text the tag tree does not hold is left alone and named (the owner's: skipped and reported, not created);
so is one holding a text that is a branch of the tree, or a tag under a face root that is no person (leaves: a tag is put
on a clip as a leaf, the person a leaf); any other has the Windows Tags it does not hold already added to its tags and its
Category cleared. A clip is left whole or moved whole: a leaf beside a branch is not moved alone, as a leaf beside a
text not in the tree is not, so its Windows Tags stay where they were for the owner to see.

Not in tagging.py: bulk_edit imports tagging, and the move is named as a bulk Tags edit (OPERATION)."""
import dataclasses
from typing import List, Optional, Tuple

from tagpup.core import fields, paths, vocabulary
from tagpup.files import exiftool_session, field_values
from tagpup.services import file_changes, tagging
from tagpup.services.bulk_edit import OPERATIONS, TAGS
from tagpup.store import db, person_ids, photos, taxonomy

#: The move's change, as History names it: a bulk Tags edit, undone as one (bulk_edit.UNDO_CHECKS).
OPERATION = OPERATIONS[TAGS]
#: What the move reads of each clip: its Windows Tags and every keyword field (tagging.KEYWORD_READ).
READ = (fields.CLIP_CATEGORY,) + tagging.KEYWORD_READ
#: Why a clip is left alone: no Windows Tags (none, an empty Xtra, or moved already).
NO_WINDOWS_TAGS = "it holds no Windows Tags"
#: Why a clip is left alone: a Windows tag it holds is no tag of the tree (the owner's: skipped, not created).
NOT_IN_TREE = "a Windows tag it holds is not in the tag tree, so it is left as it is"
#: Why a clip is left alone: a Windows tag it holds is a branch of the tag tree, or under a face root no person (leaves).
NOT_A_LEAF = "a Windows tag it holds is a branch of the tag tree, or under a people root no person, so it is left as it is"
#: How many clips the survey reads at a time, saying how far it has got after each.
CHUNK = 250


def windows_tags(held):
    """The Windows Tags of `held` (what a read of READ gives of a clip), as texts."""
    return [str(text) for text in fields.field_values(held.get(fields.CLIP_CATEGORY))]


def leaves(tree, people, roots):
    """The tags of the tag tree `tree` (taxonomy.tags) the move may put on a clip: each with no tag under it and, under a
    face root (`roots`, taxonomy.people_roots, lowercased), a person's alone (`people`, the tags person_ids.People lists).
    A branch put on a clip would be a used tag with tags under it, and a keyword under a face root names a person
    (vocabulary.extract_refs): one that is no person (a node there marked as holding no faces) would name one by its leaf."""
    branches = {vocabulary.parent_of(tag) for tag in tree}
    return frozenset(tag for tag in tree if tag not in branches
                     and (tag in people or vocabulary.key(vocabulary.root_of(tag)) not in roots))


def read_tree(conn):
    """(every tag of the tag tree, those of them the move may write: leaves) of the library open on `conn`."""
    tree = taxonomy.tags(conn)
    people = [tag for _id, tag, _name in person_ids.read(conn).listing]
    return frozenset(tree), leaves(tree, people, taxonomy.people_roots(conn))


def planner(library, tree, writable):
    """The `plan_one` of the move (file_changes.write_fields) and of its survey: from what a clip holds (READ), skip
    NO_WINDOWS_TAGS, NOT_IN_TREE or NOT_A_LEAF, else its tags with the Windows Tags it does not hold already added after
    them (tagging.keywords_planner: nothing it holds is lost) and its Category cleared; the detail, how many tags are
    added. `tree`, every tag of the tag tree (taxonomy.tags), and `writable`, those of them it may write (leaves), read
    once a run."""
    tree, writable = frozenset(tree), frozenset(writable)

    def plan_one(path, held):
        texts = windows_tags(held)
        if not texts:
            return file_changes.skip(NO_WINDOWS_TAGS)
        if any(text not in tree for text in texts):
            return file_changes.skip(NOT_IN_TREE)
        if any(text not in writable for text in texts):
            return file_changes.skip(NOT_A_LEAF)
        added = []

        def edit(_path, held_tags):
            added[:] = [text for text in dict.fromkeys(texts) if text not in held_tags]
            return added, ()
        plan = tagging.keywords_planner(library, edit)(path, held)
        return file_changes.Plan(after={**plan.after, fields.CLIP_CATEGORY: []}, detail=len(added))
    return plan_one


@dataclasses.dataclass
class Survey:
    """What a read of every clip found, each clip counted once: `to_move` (paths, by id), `no_windows_tags`, `not_in_tree`
    ([(id, the texts the tree does not hold)]), `not_a_leaf` ([(id, the texts that are a branch or no person)]),
    `unreadable` and `away` (ids; away: on a network share that did not answer, looked at once more after its window).
    `tags`, the tags the move adds; `tree` and `leaves`, the tag tree it planned with and the tags of it it may write."""
    clips: int = 0
    to_move: List[str] = dataclasses.field(default_factory=list)
    tags: int = 0
    no_windows_tags: int = 0
    not_in_tree: List[Tuple[int, List[str]]] = dataclasses.field(default_factory=list)
    not_a_leaf: List[Tuple[int, List[str]]] = dataclasses.field(default_factory=list)
    unreadable: List[int] = dataclasses.field(default_factory=list)
    away: List[int] = dataclasses.field(default_factory=list)
    tree: frozenset = frozenset()
    leaves: frozenset = frozenset()

    def counts(self):
        """{what: how many}: the survey as the script says it. Counts only."""
        return {"clips": self.clips, "to move": len(self.to_move), "tags to add": self.tags,
                "no Windows Tags": self.no_windows_tags, "a tag not in the tree": len(self.not_in_tree),
                "a branch or non-person tag": len(self.not_a_leaf), "unreadable": len(self.unreadable),
                "away": len(self.away)}


def survey(library, exiftool_path, ids=None, on_chunk=None):
    """Read every clip of `library` (`ids`, those of them alone) and plan each (planner), writing nothing: the library
    opened read-only (db.readonly_uri), no journal settled, no file written. A clip on a share that does not answer is
    looked at once more after its window (file_changes.away_now, patient), as the move does, and left out as away; a
    read ExifTool stops answering (a share gone part-way) leaves the rest away at once (exiftool_session.StaysDead).
    `on_chunk(done, of)` after each CHUNK of clips read."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        db.begin(conn)   # one snapshot: the clips, the tree and its people as one tree edit left them
        rows = photos.clip_rows(conn)
        tree, writable = read_tree(conn)
        found = Survey(tree=tree, leaves=writable)
    finally:
        conn.close()
    if ids is not None:
        wanted = set(ids)
        rows = [(photo_id, path) for photo_id, path in rows if photo_id in wanted]
    found.clips = len(rows)
    away = file_changes.away_now([path for _id, path in rows], patient=True)
    found.away = [photo_id for photo_id, path in rows if paths.key(path) in away]
    asked = [(photo_id, path) for photo_id, path in rows if paths.key(path) not in away]
    plan_one = planner(library, found.tree, found.leaves)
    with field_values.session(exiftool_path) as session:
        et = exiftool_session.StaysDead(session)
        for start in range(0, len(asked), CHUNK):
            chunk = asked[start:start + CHUNK]
            held = field_values.read(et, [path for _id, path in chunk], list(READ))
            for photo_id, path in chunk:
                now = held.get(paths.key(path))
                if isinstance(now, field_values.NotAnswered):
                    found.away.append(photo_id)
                elif now is None or isinstance(now, Exception):
                    found.unreadable.append(photo_id)
                else:
                    _surveyed(found, photo_id, path, now, plan_one(path, now))
            if on_chunk is not None:
                on_chunk(start + len(chunk), len(asked))
    return found


def _surveyed(found, photo_id, path, now, plan):
    """Count one clip's plan into `found`."""
    if plan.skip == NO_WINDOWS_TAGS:
        found.no_windows_tags += 1
    elif plan.skip == NOT_IN_TREE:
        found.not_in_tree.append((photo_id, [text for text in windows_tags(now) if text not in found.tree]))
    elif plan.skip == NOT_A_LEAF:
        found.not_a_leaf.append((photo_id, [text for text in windows_tags(now) if text not in found.leaves]))
    else:
        found.to_move.append(path)
        found.tags += plan.detail


def move(library, exiftool_path, surveyed, limit: Optional[int] = None, on_planned=None, on_chunk=None):
    """Move the Windows Tags of the clips `surveyed` found to move (the first `limit` of them), as ONE change of the
    library's journal (file_changes.write_fields, uncapped and patient as the caption sweep is: its chunks, Ctrl+C
    honoured, each clip read again and planned again by the same planner before its write, a clip changed since a
    conflict). A Result; details["counts"]: `wrote` (result.changed: the clips whose file holds the move, a read back
    that found one unchanged subtracted), `tags added` (of those), and why each other was left alone: `no Windows
    Tags`, `a tag not in the tree`, `a branch or non-person tag` (its Windows Tags changed since the survey),
    `unreadable`, `away`; `refused` (its writer refused it before a byte was written: a
    clip open in another program, details[file_changes.WRITER_REFUSED]), `conflicts` and `failed`. A re-run continues:
    a clip moved holds no Windows Tags."""
    chosen = surveyed.to_move if limit is None else surveyed.to_move[:limit]
    result = file_changes.write_fields(library, OPERATION, exiftool_path, chosen, list(READ),
                                       planner(library, surveyed.tree, surveyed.leaves),
                                       summary={"photos": len(chosen)}, unreadable="skip", uncapped=True, patient=True,
                                       on_planned=on_planned, on_chunk=on_chunk)
    away = {paths.key(path) for path in result.details.get("not_answered", [])}
    counts = {"wrote": result.changed,
              "tags added": sum(detail for detail in result.details.get("written", {}).values()
                                if isinstance(detail, int)),
              "no Windows Tags": 0, "a tag not in the tree": 0, "a branch or non-person tag": 0, "unreadable": 0,
              "away": 0}
    for path, why in result.skipped:
        counts["away" if paths.key(path) in away else "no Windows Tags" if why == NO_WINDOWS_TAGS
               else "a tag not in the tree" if why == NOT_IN_TREE
               else "a branch or non-person tag" if why == NOT_A_LEAF else "unreadable"] += 1
    refused = len(result.details.get(file_changes.WRITER_REFUSED, []))
    conflicts = len(result.details.get("conflicts", []))
    counts.update(refused=refused, conflicts=conflicts, failed=len(result.errors) - refused - conflicts)
    result.details["counts"] = counts
    return result
