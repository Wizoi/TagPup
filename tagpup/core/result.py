"""What a write did. Every service returns one.

Writes reported what they attempted, not what they changed, and it hid failures: a
backfill read 60 photos, reported 60 done and wrote nothing, because the paths did not
match and nothing said so. A Result keeps the counts apart -- attempted, changed,
skipped with why, failed with how -- and carries whatever else the caller needs back,
such as a photo's new mtime.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class Result:
    attempted: int = 0
    changed: int = 0
    #: (what, why): left alone on purpose, and not a failure.
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    #: (what, the error): tried and failed.
    errors: List[Tuple[str, str]] = field(default_factory=list)
    #: Anything else the caller needs back, by name.
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self):
        """Nothing failed. Something may still have been skipped."""
        return not self.errors

    def skip(self, what, why):
        self.skipped.append((str(what), str(why)))

    def fail(self, what, error):
        self.errors.append((str(what), str(error)))

    def message(self):
        """The errors, for a person to read."""
        return "; ".join(error for _, error in self.errors)
