"""The tag tree, and changing a tag everywhere it lives.

A tag lives in the photo files, in the index's copy of each photo's tags, and in the tree
(tag_taxonomy); a person's name is also on the faces named for them and in each photo's
list of people. The tree's edits were TagPup's route handlers, each with SQL of its own,
and TagTuner's merge and person rename were copies of their own again. Each reached a
different set of those places (docs/findings.md, #38). They all come here now, and
through _retag and _rename_in_place.
"""
import logging
import os

from tagpup.core import vocabulary
from tagpup.core.result import NotFound, Result
from tagpup.services import tagging
from tagpup.store import db, people, photos, taxonomy

logger = logging.getLogger(__name__)

#: The name TagTuner shows the faces nobody is named on. Nobody can be called it.
UNMATCHED = "Unmatched"


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
    if not name:
        result.refuse("Tag name cannot be empty")
        return result
    problem = vocabulary.problem_with_tag(name)
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
    node_id = db.write_with_connection(
        library.path, lambda conn: taxonomy.add_path(conn, tag, root_has_face=has_face),
        label="tag tree: add %s" % tag)
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
    result.changed = db.write_with_connection(
        library.path, lambda conn: taxonomy.set_branch_flags(conn, node["tag"], has_face, hidden),
        label="tag tree: flags of %s" % node["tag"])
    taxonomy.forget_people_paths(library.path)
    return result


def usage(library, node_id):
    """The photos carrying a node's tag or one under it, which deleting it would change:
    {"tag", "used", "count", "affected_photos"}, the last the first 100 of them."""
    node = _node(library, node_id)
    carrying = list(photos.carrying(library.path, node["tag"]))
    return {"tag": node["tag"], "used": bool(carrying), "count": len(carrying),
            "affected_photos": carrying[:100]}


def delete(library, node_id, action, target, exiftool_path):
    """Take a node, and every node under it, out of the tree and off the photos carrying
    them: removed, or with `action` "move", replaced by the tag `target`, under which
    the branch goes on in the tree. Deleting in the tree view.

    A photo that could not be rewritten still carries the tag, so the tag still describes
    it and stays in the tree, and the Result fails. It used to be deleted anyway, and the
    reply said success.

    details: `tag`, `photos_affected`, `photos_rewritten`.
    """
    node = _node(library, node_id)
    old = node["tag"]
    result = Result(attempted=1)
    carrying = photos.carrying(library.path, old)
    new = None
    if action == "move" and carrying:
        new = vocabulary.normalize(target or "")
        if not new:
            result.refuse("Target tag path is required for move action")
            return result
        if _under(new, old):
            result.refuse("A tag cannot be moved under itself.")
            return result
    _retag(library, old, new, list(carrying), exiftool_path, result)
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
    problem = vocabulary.problem_with_name(new_name)
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

    _rename_in_place(library, old, new, exiftool_path, result)
    # Faces keep the bare name, so renaming a person in the tree follows through to them.
    # Without this the tree, the files and the photos table all say the new name while
    # every matched face still says the old one, and TagTuner keeps showing it.
    old_leaf, new_leaf = vocabulary.leaf_of(old), vocabulary.leaf_of(new)
    if node["has_face"] and old_leaf != new_leaf:
        result.details["faces_renamed"] = _rename_person_records(library, old_leaf, new_leaf)
    _tree_changed(library)
    return result


def merge(library, source, target, exiftool_path, retire=False, apply=False):
    """Rename the tag `source` to `target` everywhere it lives, joining it with `target`
    where that is a tag already -- or, with `retire`, take it off everything. TagTuner's
    Rename, Merge and Retire.

    Without `apply` nothing is written, and details is the plan: `photos`,
    `photos_already_carrying_the_target`, `embeddings_to_drop`, `taxonomy_rows_to_drop`
    and `examples`. Applied, a photo that could not be rewritten still carries the tag, so
    the tree keeps it and the Result fails; the tag's cached CLIP embedding is dropped.

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
    problem = target and vocabulary.problem_with_tag(target)
    if problem:
        result.refuse(problem)
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
    if not apply:
        return result

    if _retag(library, source, target or None, list(carrying), exiftool_path, result, always_move=True):
        db.write_with_connection(library.path, lambda conn: taxonomy.forget_tag_embeddings(conn, source),
                                 label="tag embeddings of %s" % source)
        result.details["applied"] = True
        logger.info("Merged tag %r into %r across %d photo(s)", source, target or "(nothing)", len(carrying))
    else:
        logger.warning("Tag merge of %r: %s", source, result.message())
    return result


def rename_person(library, old_name, new_name, exiftool_path):
    """Rename a person everywhere: the faces named for them, each photo's list of people,
    each node of the tree filed under their name, and the photos carrying those tags.
    TagTuner's Rename Person.

    Into the name of a node there already, the two become one: the photos are rewritten
    first, and the old node goes once none carries it. A photo that could not be
    rewritten still names the person the old way; the Result fails for it.

    details: `photos_affected`, `photos_rewritten`, `faces_renamed`.
    """
    result = Result(attempted=1)
    old_name, new_name = str(old_name or "").strip(), str(new_name or "").strip()
    if not old_name or not new_name:
        result.refuse("Missing old_name or new_name")
        return result
    result.details.update(photos_affected=0, photos_rewritten=0, faces_renamed=0)
    if old_name == new_name:
        return result
    if UNMATCHED in (old_name, new_name):
        result.refuse("Cannot rename to/from '%s'" % UNMATCHED)
        return result
    problem = vocabulary.problem_with_name(new_name)
    if problem:
        result.refuse(problem)
        return result
    if not os.path.exists(library.path):
        raise NotFound("Database not found")

    result.details["faces_renamed"] = _rename_person_records(library, old_name, new_name)
    # Follow the rename into the tree and the photo files themselves. This once touched
    # only the faces and photos tables, so nothing was written to disk and the next scan
    # of the folder brought the old name back from the files.
    try:
        for node in taxonomy.people_nodes(library.path, old_name):
            old_tag = node["tag"]
            new_tag = vocabulary.with_leaf(old_tag, new_name)
            if taxonomy.find(library.path, new_tag):
                # Two spellings of one person becoming one. This used to leave the tree
                # and the files alone, so the files kept the old path and the next scan
                # brought the old name back.
                _retag(library, old_tag, new_tag, list(photos.carrying(library.path, old_tag)),
                       exiftool_path, result, add_up=True)
            else:
                _rename_in_place(library, old_tag, new_tag, exiftool_path, result, add_up=True)
    except Exception as e:
        # The faces already have the new name; what is left is said, not thrown.
        logger.error("Person rename: failed to update the tree or the photo files: %s", e)
        result.fail(old_name, e)
    _tree_changed(library)
    return result


def _retag(library, old, new, carrying, exiftool_path, result, always_move=False, add_up=False):
    """Take the tag `old` off the photos in `carrying` -- replaced by `new`, if given --
    and then out of its place in the tree: its branch moved under `new`, joining the
    nodes there, or taken out.

    The tree changes only once every photo is rewritten: a photo that still carries the
    tag is still described by it. Without photos, a delete takes the branch out, unless
    `always_move` (a rename: the branch goes to its new place anyway). Fails the Result
    for photos not rewritten. Returns whether the tree was changed.
    """
    rewritten = 0
    if carrying:
        if new:
            db.write_with_connection(library.path, lambda conn: taxonomy.add_path(conn, new),
                                     label="tag tree: add %s" % new)
            # The writes resolve names against the tree, which now has the target.
            taxonomy.forget_people_paths(library.path)
        rewritten = tagging.replace_tag(library, carrying, old, new, exiftool_path).changed
    _count(result, len(carrying), rewritten, add_up)
    if rewritten < len(carrying):
        result.fail(old, "%d of %d photo(s) could not be rewritten, so '%s' was kept; they "
                         "still carry it." % (len(carrying) - rewritten, len(carrying), old))
        return False
    moving = new and (carrying or always_move)
    db.write_with_connection(
        library.path,
        lambda conn: taxonomy.move_branch(conn, old, new) if moving else taxonomy.delete_branch(conn, old),
        label="tag tree: %s %s" % ("move" if moving else "delete", old))
    result.changed = 1
    _tree_changed(library)
    return True


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
    rewritten = tagging.replace_tag(library, carrying, old, new, exiftool_path).changed if carrying else 0
    result.changed = 1
    _count(result, len(carrying), rewritten, add_up)
    if rewritten < len(carrying):
        result.fail(old, "%d of %d photo(s) could not be rewritten and still carry '%s'."
                    % (len(carrying) - rewritten, len(carrying), old))


def _rename_person_records(library, old, new):
    """The person `old` renamed `new` on their faces and in each photo's list of people.
    Returns the faces renamed."""
    faces_renamed, _ = db.write_with_connection(
        library.path, lambda conn: people.rename(conn, old, new), label="rename a person's faces")
    if faces_renamed:
        logger.info("Renamed %d resolved face(s).", faces_renamed)
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


def _node(library, node_id):
    node = taxonomy.node(library.path, node_id)
    if not node:
        raise NotFound("Tag not found")
    return node


def _tree_changed(library):
    """What follows every edit of the tree: its JSON file kept in step, and what was
    read of its people forgotten."""
    taxonomy.export_json(library.path)
    taxonomy.forget_people_paths(library.path)
