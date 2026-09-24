"""The tag tree, and changing a tag everywhere it lives.

A tag lives in the photo files, in the index's copy of each photo's tags, and in the tree
(tag_taxonomy); a person's name is also on the faces named for them and in each photo's
list of people. The tree's edits were TagPup's route handlers, each with SQL of its own.
"""
import logging

from tagpup.core import vocabulary
from tagpup.core.result import NotFound, Result
from tagpup.services import tagging
from tagpup.store import db, people, photos, taxonomy

logger = logging.getLogger(__name__)


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
    carrying = photos.carrying(library.path, node["tag"])
    return {"tag": node["tag"], "used": bool(carrying), "count": len(carrying),
            "affected_photos": carrying[:100]}


def delete(library, node_id, action, target, exiftool_path):
    """Take a node, and every node under it, out of the tree and off the photos carrying
    them: removed, or with `action` "move", replaced by the tag `target`, which the tree
    gains. Deleting in the tree view.

    A photo that could not be rewritten still carries the tag, so the tag still
    describes it and stays in the tree, and the Result fails. It used to be deleted
    anyway, and the reply said success.

    details: `tag`, `photos_affected`, `photos_rewritten`.
    """
    node = _node(library, node_id)
    old = node["tag"]
    result = Result(attempted=1)
    affected = photos.carrying(library.path, old)
    rewritten = 0
    if affected:
        new = None
        if action == "move":
            new = vocabulary.normalize(target or "")
            if not new:
                result.refuse("Target tag path is required for move action")
                return result
            db.write_with_connection(library.path, lambda conn: taxonomy.add_path(conn, new),
                                     label="tag tree: add %s" % new)
            # The writes resolve names against the tree, which now has the target.
            taxonomy.forget_people_paths(library.path)
        rewritten = tagging.replace_tag(library, affected, old, new, exiftool_path).changed

    result.details.update(tag=old, photos_affected=len(affected), photos_rewritten=rewritten)
    if rewritten < len(affected):
        result.fail(old, "%d of %d photo(s) could not be rewritten, so '%s' was kept; they "
                         "still carry it." % (len(affected) - rewritten, len(affected), old))
        return result
    db.write_with_connection(library.path, lambda conn: taxonomy.delete_branch(conn, old),
                             label="tag tree: delete %s" % old)
    result.changed = 1
    _tree_changed(library)
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

    db.write_with_connection(library.path, lambda conn: taxonomy.move_branch(conn, old, new),
                             label="tag tree: rename %s" % old)
    # The tree has the new name now. The writes below resolve people against it, and with
    # the cache still holding the old tree a photo that also carries the bare name had the
    # old path written straight back.
    taxonomy.forget_people_paths(library.path)
    affected = photos.carrying(library.path, old)
    rewritten = tagging.replace_tag(library, affected, old, new, exiftool_path).changed if affected else 0

    # Faces keep the bare name, so renaming a person in the tree follows through to them.
    # Without this the tree, the files and the photos table all say the new name while
    # every matched face still says the old one, and TagTuner keeps showing it.
    faces_renamed = 0
    old_leaf, new_leaf = vocabulary.leaf_of(old), vocabulary.leaf_of(new)
    if node["has_face"] and old_leaf != new_leaf:
        faces_renamed, _ = db.write_with_connection(
            library.path, lambda conn: people.rename(conn, old_leaf, new_leaf),
            label="rename %s's faces" % old_leaf)
        if faces_renamed:
            logger.info("Tag rename also renamed %d resolved face(s).", faces_renamed)

    result.changed = 1
    result.details.update(photos_affected=len(affected), photos_rewritten=rewritten,
                          faces_renamed=faces_renamed)
    if rewritten < len(affected):
        result.fail(old, "%d of %d photo(s) could not be rewritten and still carry '%s'."
                    % (len(affected) - rewritten, len(affected), old))
    _tree_changed(library)
    return result


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
