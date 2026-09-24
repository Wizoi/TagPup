"""The people a library knows."""
from tagpup.store import people


def names(library, keywords_too=False, include_hidden=False):
    """The people the pages offer while a name is typed (tagpup.store.people.names):
    TagPup's are the names given to faces; TagTuner's add the people keywords name."""
    return people.names(library.path, keywords_too=keywords_too, include_hidden=include_hidden)
