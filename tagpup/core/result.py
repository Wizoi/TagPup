"""What a write did. Every service that writes returns one.

Writes reported what they attempted, not what they changed, and it hid failures: a
backfill read 60 photos, reported 60 done and wrote nothing, because the paths did not
match and nothing said so. A Result keeps the counts apart -- attempted, changed,
skipped with why, failed with how -- and carries whatever else the caller needs back,
such as a photo's new mtime.

A service that reads returns what it read, and raises NotFound or Refused where a write
would have skipped or refused.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: The kinds of refusal (Result.refused_kind). REFUSED, the default: the request is not one that is made (a route's 400).
REFUSED = "refused"
#: A folder's network share did not answer: a web route answers 503 (try again later), not the 400 of a folder that is
#: not there (tagpup.files.shares.folder_refusal, #2129).
SHARE_AWAY = "share_away"
#: The file is no longer the one the page built its record from (the stamp the page sent differs from the file's): a
#: web route answers 409, and the page reads the file again.
CHANGED_ON_DISK = "changed_on_disk"


@dataclass
class Result:
    attempted: int = 0
    changed: int = 0
    #: (what, why): left alone on purpose, and not a failure.
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    #: (what, the error): tried and failed.
    errors: List[Tuple[str, str]] = field(default_factory=list)
    #: Why the request was refused before anything was written -- a tag that may not be
    #: set, say. A web route answers it with 400, where an error is a 500.
    refused: Optional[str] = None
    #: What kind of refusal it is (REFUSED, SHARE_AWAY, CHANGED_ON_DISK), which the web route's status is read from
    #: (tagpup.web.responses.STATUS). A step that hands up a refusal whose source can refuse with a kind (shares.folder_refusal, tagging.save_photo) hands the kind up with it; the others' sources only refuse as REFUSED.
    refused_kind: str = REFUSED
    #: Anything else the caller needs back, by name.
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self):
        """Nothing failed or was refused. Something may still have been skipped."""
        return not self.errors and not self.refused

    def skip(self, what, why):
        self.skipped.append((str(what), str(why)))

    def fail(self, what, error):
        self.errors.append((str(what), str(error)))

    def refuse(self, why, kind=REFUSED):
        """Refuse it for `why`, a sentence for the owner, as `kind` (REFUSED unless a route must answer otherwise)."""
        self.refused = str(why)
        self.refused_kind = kind

    def message(self):
        """Why it was refused, or the errors, for a person to read."""
        if self.refused:
            return self.refused
        return "; ".join(error for _, error in self.errors)




#: What a Result refused as CHANGED_ON_DISK says.
CHANGED_ON_DISK_SENTENCE = "This photo changed on disk since you opened it: reload it first."


class NotFound(Exception):
    """What a read or a write was asked about is not there. A web route answers it
    with 404."""


class Conflict(Exception):
    """A write that cannot be made while things stand as they do: naming a face that has
    been excluded, say. A web route answers it with 409."""


class NotHeld(Conflict):
    """A row asked for in a folder the library does not hold, and was not asked to add
    (tagpup.store.photos.ensure_row). `folder` is the folder, as stored."""

    def __init__(self, folder):
        self.folder = folder
        super().__init__("%s is not in this library: add the folder to it first" % folder)


class Refused(Exception):
    """A read that will not be answered: a file type the servers do not send, say. A web
    route answers it with 400, as it does a Result that was refused."""


class Unavailable(Exception):
    """A read that cannot be answered just now, and why, as a sentence: the photo's file is on a network share that is
    away, or is there and refuses to be read. A web route answers it with 503 and Retry-After: ask again in a moment.
    Raised by tagpup.files.shares (the one owner of "a share is away") and the services reading through it."""
