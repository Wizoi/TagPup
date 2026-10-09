"""The tag tree, and changing a tag everywhere it lives.

A tag lives in the photo files, in the index's copy of each photo's tags, and in the tree
(tag_taxonomy); a person is a node of the tree, named on the faces and in each photo's list of people BY THE
NODE'S ID, with the name beside it as a cache of the node's leaf (docs/ARCHITECTURE.md, "People by id, stage 2"). The
tree's edits were TagPup's route handlers, each with SQL of its own,
and TagTuner's merge and person rename were copies of their own again. Each reached a
different set of those places (docs/findings.md, #38). They all come here now, and
through _retag and _rename_in_place.

**One transaction for the database, the files after.** Renaming a person moves the one node (its id stays, so every face and
listed person follows it and their cached name is refreshed in the same transaction: no moment has a face with a name no node
has), then the photo files are rewritten by the keyword writer's own machinery, which is resumable. A move between groups
keeps the id. A merge puts everything that names the one tag on the other before the tag's node goes, in the tree edit that
joins them (taxonomy.move_branch). Deleting a person that faces name is refused with the count, and `force` unnames those
faces in the same transaction as the node goes (taxonomy.delete_branch). A tag put under a person that faces or photos carry
is refused (rule a, taxonomy.refuse_child_of_person).
"""
import logging
import os

from tagpup.core import validation, vocabulary
from tagpup.core.result import NotFound, Result
from tagpup.services import people as people_service
from tagpup.services import tagging
from tagpup.store import db, faces, people, person_ids, photos, taxonomy

logger = logging.getLogger(__name__)

#: The name TagTuner shows the faces nobody is named on (tagpup.core.vocabulary).
UNMATCHED = vocabulary.UNMATCHED


def tree(library):
    """Every node of the tag tree, with `usage_count`: the photos carrying it or a tag
    under it. The tree view. A library without the tree's table is seeded first, and
    what older writers left wrong in it is put right."""
    if not taxonomy.has_tree(library.path):
        taxonomy.seed(library.path)
    taxonomy.repair(library.path)
    counts = photos.tag_usage(library.path)
    return [dict(node, usage_count=counts.get(node["tag"], 0)) for node in taxonomy.nodes(library.path)]


def create(library, name, parent_id=None, has_face=0):
    """Add a tag to the tree: `name`, below the node `parent_id` if given, one node per
    level. Adding a tag in the tree view.

    `name` may itself be a path. Typed as the whole path from the root, it does not
    repeat the levels the parent already is. `has_face` is for a new root; a node made
    below another takes its parent's flag. Adding a tag already there changes nothing.

    details: `id` and `tag`, the node of the tag and its path.
    """
    result = Result(attempted=1)
    name = (name or "").strip()
    problem = validation.problem("tag", name)
    if problem:
        result.refuse(problem)
        return result

    # The name goes below the parent, one node per level. This used to walk the parent's
    # own levels again below it, so "Jane" under Crew/Divers also made Crew/Divers/Crew,
    # Crew/Divers/Crew/Divers and Crew/Divers/Crew/Divers/Jane.
    levels, above = vocabulary.segments(name), []
    if parent_id:
        parent = taxonomy.node(library.path, parent_id)
        if not parent:
            raise NotFound("Parent tag not found")
        above = vocabulary.segments(parent["tag"])
        if [vocabulary.key(p) for p in levels[:len(above)]] == [vocabulary.key(p) for p in above]:
            levels = levels[len(above):]
    tag = vocabulary.SEPARATOR.join(above + levels)

    existed = taxonomy.find(library.path, tag)

    def add(conn):
        taxonomy.refuse_child_of_person(conn, tag)
        return taxonomy.add_node(conn, tag, root_has_face=has_face)

    try:
        node_id = db.write_with_connection(library.path, add, label="tag tree: add %s" % tag)
    except person_ids.PersonHasNoChildren as why:
        result.refuse(str(why))
        return result
    if not existed:
        result.changed = 1
        _tree_changed(library)
    result.details.update(id=node_id, tag=tag)
    return result


def set_flags(library, node_id, has_face=None, hidden=None):
    """Mark a node, and every node under it, as holding faces or hidden from autocomplete,
    or not: the switches in the tree view. None leaves a flag as it is.

    `changed`: the nodes the branch holds.
    """
    node = _node(library, node_id)
    result = Result(attempted=1)
    if has_face == 0 and _last_face_root(library, node["tag"]):
        result.refuse(_LAST_FACE_ROOT % node["tag"])
        return result
    result.changed = db.write_with_connection(
        library.path, lambda conn: taxonomy.set_branch_flags(conn, node["tag"], has_face, hidden),
        label="tag tree: flags of %s" % node["tag"])
    taxonomy.forget_people_paths(library.path)
    return result


def usage(library, node_id):
    """The photos carrying a node's tag or one under it, which deleting it would change:
    {"tag", "used", "count", "affected_photos", "faces_named", "history_rows_if_forced"}, the photos the first 100 of them and
    `faces_named` how many faces name the node or a person under it (deleting needs `force`, which unnames them and records them
    in History: `history_rows_if_forced`, three rows a face)."""
    node = _node(library, node_id)
    carrying = list(photos.carrying(library.path, node["tag"]))
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        named = sum(taxonomy.faces_named_by(conn, node["tag"]).values())
    finally:
        conn.close()
    return {"tag": node["tag"], "used": bool(carrying), "count": len(carrying),
            "affected_photos": carrying[:100], "faces_named": named, "history_rows_if_forced": 3 * named}


def delete(library, node_id, action, target, exiftool_path, force=False):
    """Take a node, and every node under it, out of the tree and off the photos carrying
    them: removed, or with `action` "move", replaced by the tag `target`, under which
    the branch goes on in the tree. Deleting in the tree view.

    A person that faces are named by is not deleted: refused with the count of faces and nothing written, unless `force`,
    which unnames those faces in the same transaction as the node goes (the photos' keywords are taken off, as for any tag).
    A moved person keeps their id, and their faces.

    A photo that could not be rewritten still carries the tag, so the tag still describes
    it and stays in the tree, and the Result fails. It used to be deleted anyway, and the
    reply said success.

    details: `tag`, `photos_affected`, `photos_rewritten`.
    """
    node = _node(library, node_id)
    old = node["tag"]
    result = Result(attempted=1)
    if _last_face_root(library, old):
        result.refuse(_LAST_FACE_ROOT % old)
        return result
    carrying = photos.carrying(library.path, old)
    new = None
    if action == "move" and carrying:
        problem = validation.problem("tag", target)
        if problem:
            result.refuse(problem)
            return result
        new = vocabulary.normalize(target)
        if _under(new, old):
            result.refuse("A tag cannot be moved under itself.")
            return result
        if _refuse_under_person(library, new, result):
            return result
    if not new and not force and _refuse_if_named(library, old, result):
        return result
    _retag(library, old, new, list(carrying), exiftool_path, result, force=force)
    result.details["tag"] = old
    return result


def rename(library, node_id, new_name, exiftool_path):
    """Give a node a new name: the node, the nodes under it, and every photo carrying any
    of them. For a node holding faces, the faces named for it and each photo's list of
    people too. Renaming in the tree view.

    Refused when the new path is a node already: this does not make two nodes one. A
    photo that could not be rewritten keeps the old tag; the Result fails for it, and the
    tree keeps the new name.

    details: `photos_affected`, `photos_rewritten`, `faces_renamed`.
    """
    result = Result(attempted=1)
    new_name = (new_name or "").strip()
    # A node's own name is one level. A "/" in it gave the node a path deeper than its
    # parent's by two levels, with no node between, and a name that was a path.
    problem = validation.problem("name", new_name)
    if problem:
        result.refuse(problem)
        return result
    node = _node(library, node_id)
    result.details.update(photos_affected=0, photos_rewritten=0, faces_renamed=0)
    if node["name"] == new_name:
        return result
    parent = None
    if node["parent_id"] is not None:
        parent = taxonomy.node(library.path, node["parent_id"])
        if not parent:
            raise RuntimeError("Parent tag not found in DB")
    old = node["tag"]
    new = vocabulary.normalize((parent["tag"] + vocabulary.SEPARATOR if parent else "") + new_name)
    if taxonomy.find(library.path, new):
        result.refuse("A tag with path '%s' already exists." % new)
        return result

    # The faces named for the node are named by its id: the tree edit gives them the new name in its own transaction (the
    # cache of a leaf), so nothing is left saying the old one. Counted AFTER the write: the faces of the people under the
    # node (a group's faces are those of everyone under it; their path changed), and for a person, those that now carry the
    # new name.
    under = [each["id"] for each in taxonomy.branch(library.path, old)] if node["has_face"] else []
    _rename_in_place(library, old, new, exiftool_path, result)
    leaf = len(under) == 1
    result.details["faces_renamed"] = _faces_of(library, under, named=vocabulary.leaf_of(new) if leaf else None)
    _tree_changed(library)
    return result


def merge(library, source, target, exiftool_path, retire=False, apply=False, force=False):
    """Rename the tag `source` to `target` everywhere it lives, joining it with `target`
    where that is a tag already -- or, with `retire`, take it off everything. TagTuner's
    Rename, Merge and Retire.

    Without `apply` nothing is written, and details is the plan: `photos`,
    `photos_already_carrying_the_target`, `embeddings_to_drop`, `taxonomy_rows_to_drop`
    and `examples`. Applied, a photo that could not be rewritten still carries the tag, so
    the tree keeps it and the Result fails; the tag's cached CLIP embedding is dropped.

    When the two tags are people (faces name them), the plan says how many photos would list the one person twice
    (`photos_listing_both`: their list names both and the merge makes it name them once) and how many have a face of each
    (`photos_with_a_face_of_each`: both faces stay named). Retiring a person that faces name is refused with the count unless
    `force`, which unnames the faces.

    details, applied: `applied`, `photos_rewritten`.
    """
    result = Result(attempted=1)
    source, target = (source or "").strip(), (target or "").strip()
    if not source:
        result.refuse("Missing the tag to change")
        return result
    if not target and not retire:
        result.refuse("Missing the tag to merge into")
        return result
    problem = target and validation.problem("tag", target)
    if problem:
        result.refuse(problem)
        return result
    if _last_face_root(library, source) and (
            retire or vocabulary.root_of(target) not in taxonomy.face_roots(library.path)):
        result.refuse(_LAST_FACE_ROOT % source)
        return result
    # In its one spelling, as the tag tree holds it: "School / Kentridge" was written into
    # the files with its spaces.
    target = vocabulary.normalize(target)
    if target == source:
        result.refuse("That tag is already called that")
        return result
    if target and _under(target, source):
        result.refuse("A tag cannot be moved under itself.")
        return result

    if target and _refuse_under_person(library, target, result):
        return result
    carrying = photos.carrying(library.path, source)
    result.details.update({
        "from": source, "into": target or None, "retire_only": retire,
        "photos": len(carrying),
        "photos_already_carrying_the_target": sum(1 for tags in carrying.values() if target and target in tags),
        "embeddings_to_drop": taxonomy.tag_embeddings(library.path, source),
        "taxonomy_rows_to_drop": len(taxonomy.branch(library.path, source)),
        "examples": [os.path.basename(p) for p in list(carrying)[:5]],
        "applied": False,
    })
    result.details.update(_shared_by(library, source, target) if target else {})
    if retire and not force and _refuse_if_named(library, source, result):
        return result
    if not apply:
        return result

    if _retag(library, source, target or None, list(carrying), exiftool_path, result, always_move=True, force=force):
        db.write_with_connection(library.path, lambda conn: taxonomy.forget_tag_embeddings(conn, source),
                                 label="tag embeddings of %s" % source)
        result.details["applied"] = True
        logger.info("Merged tag %r into %r across %d photo(s)", source, target or "(nothing)", len(carrying))
    else:
        logger.warning("Tag merge of %r: %s", source, result.message())
    return result


def rename_person(library, person, new_name, exiftool_path):
    """Rename ONE person: the one picked -- `person` is the id of their node, or a tag path or a name (a name two people have
    is refused, naming them) -- everywhere: their node in the tree (its id stays, so their faces and the photos' lists follow it
    and take the new name in the same transaction), and the photos carrying their tag. TagTuner's Rename Person.

    It never merges: a person already filed at the new place is refused ("merge them instead", tags.merge). Other people
    called the same are other people and are left. A photo that could not be rewritten still names the person the old way;
    the Result fails for it. A name no person tag has -- faces named with no node -- renames those faces alone.

    details: `photos_affected`, `photos_rewritten`, `faces_renamed`.
    """
    result = Result(attempted=1)
    new_name = str(new_name or "").strip()
    if person is None or (isinstance(person, str) and not person.strip()):
        result.refuse("Missing old_name or new_name")
        return result
    result.details.update(photos_affected=0, photos_rewritten=0, faces_renamed=0)
    # The new name is held to a name's rules, which refuse "Unmatched" as well.
    problem = validation.problem("name", new_name)
    if problem:
        result.refuse(problem)
        return result
    if isinstance(person, str) and person.strip() == UNMATCHED:
        result.refuse("Cannot rename to/from '%s'" % UNMATCHED)
        return result
    if not os.path.exists(library.path):
        raise NotFound("Database not found")

    found = people_service.resolve(library, person)   # NotFound for a stale id; Refused naming the candidates
    if found is None:
        old_name = str(person).strip()
        if old_name == new_name:
            return result
        result.details["faces_renamed"] = _rename_unnamed_records(library, old_name, new_name)
        _tree_changed(library)
        return result
    if found.name == new_name:
        return result
    new_tag = vocabulary.with_leaf(found.tag, new_name)
    there = taxonomy.find(library.path, new_tag)
    if there and there["id"] != found.id:
        result.refuse("A person is filed at '%s' already: merge them instead (Merge tags), or choose another name." % new_tag)
        return result
    alone = _alone(library, found)   # before the tree moves: a bare keyword spelled so is theirs only if nobody else is called so
    try:
        # The node first, with its faces' cache in the same transaction; then the photo files.
        _rename_in_place(library, found.tag, new_tag, exiftool_path, result)
        result.details["faces_renamed"] = _faces_of(library, [found.id], named=new_name)   # counted after the write
        # A photo naming them by the bare name carries no tag of the tree's, and was not rewritten above; with the tree
        # moved, the bare name named nobody (#87). Only a name nobody else has is theirs.
        bare = list(photos.carrying(library.path, found.name)) if alone else []
        if bare:
            rewritten, unwritten = _rewrite(library, bare, found.name, new_name, exiftool_path)
            _count(result, len(bare), rewritten, add_up=True)
            if unwritten:
                result.fail(found.name, "%d of %d photo(s) naming '%s' could not be rewritten."
                            % (unwritten, len(bare), found.name))
    except Exception as e:
        # The node and the faces already have the new name; what is left is said, not thrown.
        logger.error("Person rename: failed to update the photo files: %s", e)
        result.fail(found.name, e)
    _tree_changed(library)
    return result


def _faces_of(library, person_ids_, named=None):
    """How many faces name any of the people `person_ids_` -- and, with `named`, now carry that name -- read now."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return faces.count_of_people(conn, person_ids_, named)
    finally:
        conn.close()


def _alone(library, found):
    """Is nobody else called what `found` (a Person) is? A bare keyword spelled so is theirs only then."""
    return people_service.directory(library).of_name(found.name)["shared"] is False


def _shared_by(library, source, target):
    """What merging the tag `source` into the tag `target` leaves of photos naming both people (see merge): {} when either is
    not a person a face or a photo's list names."""
    first, second = taxonomy.find(library.path, source), taxonomy.find(library.path, target)
    if not first or not second:
        return {}
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        listed, faced = people.sharing(conn, first["id"], second["id"])
    finally:
        conn.close()
    return {"photos_listing_both": listed, "photos_with_a_face_of_each": faced}


def _refuse_if_named(library, tag, result):
    """Refuse `result` (and say so) when faces are named by the node `tag` or a node under it: the sentence names how many."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        used = taxonomy.faces_named_by(conn, tag)
    finally:
        conn.close()
    if not used:
        return False
    result.refuse(str(person_ids.PersonInUse(sum(used.values()), sorted(used))))
    result.details["faces_named"] = sum(used.values())
    return True


def _refuse_under_person(library, tag, result):
    """Refuse `result` when the tag `tag` would be a new tag under a person that faces or photos carry (rule a)."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        taxonomy.refuse_child_of_person(conn, tag)
    except person_ids.PersonHasNoChildren as why:
        result.refuse(str(why))
        return True
    finally:
        conn.close()
    return False


def _retag(library, old, new, carrying, exiftool_path, result, always_move=False, add_up=False, force=False):
    """Take the tag `old` off the photos in `carrying` -- replaced by `new`, if given --
    and then out of its place in the tree: its branch moved under `new`, joining the
    nodes there, or taken out.

    The tree changes only once every photo is rewritten: a photo that still carries the
    tag is still described by it. Without photos, a delete takes the branch out, unless
    `always_move` (a rename: the branch goes to its new place anyway). Fails the Result
    for photos not rewritten. Returns whether the tree was changed.
    """
    rewritten = unwritten = 0
    if new and (carrying or always_move):
        # Every refusal of the tree edit comes BEFORE the first file is written: a photo rewritten to a tag the tree then
        # refuses (a person onto something that is no person) is a file and an index that disagree with the tree.
        why = _tree_refusal(library, old, new, make_target=bool(carrying))
        if why is not None:
            result.refuse(str(why))
            result.details["faces_named"] = getattr(why, "faces", 0)
            return False
    if carrying:
        if new:
            db.write_with_connection(library.path, lambda conn: taxonomy.add_node(conn, new),
                                     label="tag tree: add %s" % new)
            # The writes resolve names against the tree, which now has the target.
            taxonomy.forget_people_paths(library.path)
        rewritten, unwritten = _rewrite(library, carrying, old, new, exiftool_path)
    _count(result, len(carrying), rewritten, add_up)
    if unwritten:
        result.fail(old, "%d of %d photo(s) could not be rewritten, so '%s' was kept; they "
                         "still carry it." % (unwritten, len(carrying), old))
        return False
    moving = new and (carrying or always_move)
    try:
        db.write_with_connection(
            library.path,
            lambda conn: taxonomy.move_branch(conn, old, new) if moving else taxonomy.delete_branch(conn, old, force=force),
            label="tag tree: %s %s" % ("move" if moving else "delete", old))
    except person_ids.PersonInUse as why:
        # Faces were named for the tag between the check and the write (or a person given nowhere to go): nothing was changed
        # in the tree, and the tag stays.
        result.fail(old, str(why))
        return False
    result.changed = 1
    _tree_changed(library)
    return True


class _Rehearsed(Exception):
    """The edit rehearsed went through; it is rolled back."""


def _tree_refusal(library, old, new, make_target):
    """The PersonInUse the tree edit of `old` onto `new` would raise (the target made first when `make_target`, as _retag does),
    or None: the edit is run in a transaction that is rolled back, so the rule that refuses is the one rule, the tree's
    (taxonomy.move_branch, people.merge_person), and nothing is left behind."""
    def rehearse(conn):
        if make_target:
            taxonomy.add_node(conn, new)
        taxonomy.move_branch(conn, old, new)
        raise _Rehearsed

    try:
        db.write_with_connection(library.path, rehearse, label="tag tree: rehearse %s" % old)
    except _Rehearsed:
        return None
    except person_ids.PersonInUse as why:
        return why
    return None


def _rename_in_place(library, old, new, exiftool_path, result, add_up=False):
    """Move the branch `old` to the free place `new` in the tree, then rewrite the photos
    carrying it. A photo that could not be rewritten keeps the old tag, and the Result
    fails for it; the tree keeps the new name."""
    db.write_with_connection(library.path, lambda conn: taxonomy.move_branch(conn, old, new),
                             label="tag tree: rename %s" % old)
    # The tree has the new name now. The writes below resolve people against it, and with
    # the cache still holding the old tree a photo that also carries the bare name had the
    # old path written straight back.
    taxonomy.forget_people_paths(library.path)
    carrying = list(photos.carrying(library.path, old))
    rewritten, unwritten = _rewrite(library, carrying, old, new, exiftool_path) if carrying else (0, 0)
    result.changed = 1
    _count(result, len(carrying), rewritten, add_up)
    if unwritten:
        result.fail(old, "%d of %d photo(s) could not be rewritten and still carry '%s'."
                    % (unwritten, len(carrying), old))


def _rewrite(library, carrying, old, new, exiftool_path):
    """(rewritten, not rewritten) of the photos in `carrying` (tagging.replace_tag).

    The photos come from the index, and the rewrite reads each file first: a photo whose
    file no longer carries the tag is skipped, and its row made to say so. That photo is
    done, not one that could not be rewritten -- counted as one, a delete kept a tag no
    photo carried (docs/findings.md, #44). What is neither rewritten nor skipped -- an
    error, or a photo whose row could not be read -- may still carry it.
    """
    outcome = tagging.replace_tag(library, carrying, old, new, exiftool_path)
    return outcome.changed, len(carrying) - outcome.changed - len(outcome.skipped)


def _rename_unnamed_records(library, old, new):
    """The faces called `old` whose name no person tag has renamed `new`, and each photo's list of people. Returns the faces
    renamed."""
    try:
        faces_renamed, _ = db.write_with_connection(
            library.path, lambda conn: faces.rename_unresolved(conn, old, new), label="rename a name's faces")
    except person_ids.PersonProblem as problem:
        people_service.translate(problem)   # a new name two people have: Refused, naming them
    if faces_renamed:
        logger.info("Renamed %d face(s) with no person tag.", faces_renamed)
    return faces_renamed


def _count(result, affected, rewritten, add_up):
    """Record the photos an edit touched: added to what the Result holds with `add_up`
    (a person filed in more than one place), else in place of it."""
    if add_up:
        affected += result.details.get("photos_affected", 0)
        rewritten += result.details.get("photos_rewritten", 0)
    result.details.update(photos_affected=affected, photos_rewritten=rewritten)


def _under(tag, other):
    """Is `tag` the tag `other`, or under it?"""
    return vocabulary.retag([tag], other)[1]


#: Why the last face root stays (docs/findings.md, #66).
_LAST_FACE_ROOT = ("'%s' is the only root that holds faces: without it, nobody in the library "
                   "is a person. Mark another root as holding faces first.")


def _last_face_root(library, tag):
    """Is `tag` the library's only root that holds faces? Taking it away, or its flag,
    would leave every person a word, so the owner chose that it stays (2026-09-24)."""
    return taxonomy.face_roots(library.path) == [tag]


def _node(library, node_id):
    node = taxonomy.node(library.path, node_id)
    if not node:
        raise NotFound("Tag not found")
    return node


def _tree_changed(library):
    """What follows every edit of the tree: what was read of its people forgotten."""
    taxonomy.forget_people_paths(library.path)


# ---- The word tags, read ------------------------------------------------------------------
#
# TagTuner curates faces because a wrong name spreads: a tagged photo is what the
# suggester learns the next photo from. Word tags spread the same way and had no view at
# all, so a misspelling could sit in the vocabulary for months, be suggested, be applied,
# and become its own source. Finding one took SQL. These answer the two questions that
# were unanswerable: what is in the vocabulary, and which photos a given tag touches.

def listing(library, include_people=False):
    """Every tag this library knows, with what it touches, and the buckets worth a look:
    `flat` (no path), `used_once` (where typos hide), `unused` (in the vocabulary, on no
    photo, still feeding zero-shot matching) and `people_without_a_path`.

    `include_people` decides which side of the face/word split to return: the point of
    this view is the tags TagTuner could not previously reach, so people are left out
    by default.
    """
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        taxonomy_rows = taxonomy.face_flags(conn)
        people_roots = taxonomy.people_roots(conn)
        embedded = taxonomy.embedded_tags(conn)
        tag_lists = photos.tag_lists(conn)
    finally:
        conn.close()

    in_taxonomy = {tag: bool(has_face) for tag, has_face in taxonomy_rows if tag}

    # A person the taxonomy files under a people root, by their leaf name. A bare tag
    # matching one is that person having lost their path, not a word tag -- and saying
    # so is the point, since that is the fault the keyword convention forbids.
    person_leaves = {
        vocabulary.key(vocabulary.leaf_of(tag))
        for tag in in_taxonomy
        if "/" in tag and vocabulary.key(vocabulary.root_of(tag)) in people_roots
    }

    def is_person_tag(tag):
        if "/" in tag:
            return vocabulary.key(vocabulary.root_of(tag)) in people_roots
        low = tag.strip().lower()
        return low in people_roots or low in person_leaves or in_taxonomy.get(tag, False)

    def is_stray_person(tag):
        return "/" not in tag and tag.strip().lower() in person_leaves

    counts = {}
    for tags in tag_lists:
        for tag in tags:
            if tag:
                counts[tag] = counts.get(tag, 0) + 1

    every = set(counts) | set(in_taxonomy)
    out = []
    for tag in sorted(every, key=vocabulary.tag_sort_key):
        if is_person_tag(tag) and not include_people:
            continue
        out.append({
            "tag": tag,
            "leaf": vocabulary.leaf_of(tag),
            "count": counts.get(tag, 0),
            "flat": "/" not in tag,
            "in_taxonomy": tag in in_taxonomy,
            "has_embedding": tag in embedded,
            "is_person": is_person_tag(tag),
            "person_without_path": is_stray_person(tag),
        })

    buckets = {
        "flat": sorted((t["tag"] for t in out if t["flat"] and t["count"]), key=vocabulary.tag_sort_key),
        "used_once": sorted((t["tag"] for t in out if t["count"] == 1), key=vocabulary.tag_sort_key),
        "unused": sorted((t["tag"] for t in out if t["count"] == 0), key=vocabulary.tag_sort_key),
        # Counted separately from the tags returned: a person who lost their path is
        # excluded from the word-tag list by is_person_tag, but it is exactly what
        # somebody opening this view wants told.
        "people_without_a_path": sorted(
            (tag for tag in every if is_stray_person(tag) and counts.get(tag, 0) > 0),
            key=vocabulary.tag_sort_key),
    }
    return {"tags": out, "buckets": buckets}


def photos_carrying(library, tag):
    """The photos carrying exactly `tag`, newest first, each with its tags. Matches the
    whole tag, never a prefix."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = photos.with_tag(conn, tag)
    finally:
        conn.close()
    found = [{"path": path, "filename": os.path.basename(path), "mtime": mtime or 0, "tags": tags}
             for path, tags, mtime in rows]
    found.sort(key=lambda p: p["mtime"], reverse=True)
    return {"tag": tag, "photos": found, "total": len(found)}


def autocomplete(library):
    """Every tag offered while one is typed, alphabetical (vocabulary.tag_sort_key, the order the
    pages show tags in): the tags photos carry and the tree's nodes, less those hidden from
    autocomplete and everything under them (tagpup.core.vocabulary.hidden_by). TagPup's /api/tags."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        found = set()
        for tags in photos.tag_lists(conn):
            found.update(tags)
        found.update(taxonomy.tags(conn))
        hidden = taxonomy.hidden_tags(conn)
    finally:
        conn.close()
    return sorted((tag for tag in found if not vocabulary.hidden_by(tag, hidden)), key=vocabulary.tag_sort_key)
