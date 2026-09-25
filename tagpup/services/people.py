"""The people a library knows."""
from tagpup.core import vocabulary
from tagpup.store import db, faces, people, taxonomy


def names(library, keywords_too=False, include_hidden=False):
    """The people the pages offer while a name is typed (tagpup.store.people.names):
    TagPup's are the names given to faces; TagTuner's add the people keywords name."""
    return people.names(library.path, keywords_too=keywords_too, include_hidden=include_hidden)


def with_counts(library):
    """[{"name", "count"}]: everyone with a named face, and how many, for Review People.
    A person is left out when every node the tree files them under is hidden.

    Deliberately people only. An "Unmatched" pseudo-person used to be pinned at the
    top, which dropped a flat dump of every nameless face into a mode meant for
    auditing named people -- unordered and far too large to work through. Nameless
    faces belong to the Identify Faces queue (docs/findings.md, #49).
    """
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        hidden_tags = taxonomy.hidden_tags(conn)
        # Where the tree files everyone, in one read: it was a query per person (#50).
        filed = taxonomy.filed_people(conn)
        counted = faces.counts_by_name(conn)
    finally:
        conn.close()
    listed = []
    for name, count in counted:
        tag_paths = filed.get(name, [])
        if tag_paths and all(vocabulary.hidden_by(path, hidden_tags) for path in tag_paths):
            continue
        listed.append({"name": name, "count": count})
    return listed
