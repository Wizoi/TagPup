"""The fields of a photo record the page keeps in the browser (services.photos.page_record) beside the CACHE_VERSION
(web/tagpup/cache.js) they were made for, in tests/frontend/fixtures/page_record.json. A record that gains or loses a field
without the version moving would be served from a scan kept before it (a record without `camera` read as "Unknown Camera"),
so the Python test fails when the fields differ from the fixture and the page test (cache-version.test.mjs) fails when the
fixture's version is not cache.js's (docs/OWNERS.md, contract 22). Regenerate: `python tests/test_page_record_fields.py --write`
after raising CACHE_VERSION. Fictional names only.
"""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402

from tagpup.services import photos  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "frontend", "fixtures", "page_record.json")
CACHE_JS = os.path.join(ROOT, "web", "tagpup", "cache.js")
PHOTO = os.path.join("D:" + os.sep, "Pictures", "Coast", "IMG_0001.jpg")


def cache_version():
    with open(CACHE_JS, encoding="utf-8") as handle:
        return int(re.search(r"export const CACHE_VERSION = (\d+);", handle.read()).group(1))


def made_now():
    """The fields of a record of a photo read as the indexer reads one, and of one ExifTool could not read."""
    read = photo_rows.as_read(PHOTO, {"XMP:Subject": ["Trips/Coast"], "EXIF:DateTimeOriginal": "2024:07:04 10:00:00",
                                      "EXIF:Make": "Acme", "EXIF:Model": "Snap 3"})
    unread = dict(read, read_error="could not read")
    return {"version": cache_version(),
            "read": sorted(photos.page_record(PHOTO, read, 1.0, 2)),
            "unreadable": sorted(photos.page_record(PHOTO, unread, 1.0, 2))}


class PageRecordFieldsAreVersioned(unittest.TestCase):
    def test_a_record_of_other_fields_than_the_fixture_needs_a_new_cache_version(self):
        with open(FIXTURE, encoding="utf-8") as handle:
            kept = json.load(handle)
        now = made_now()
        self.assertEqual(
            {key: kept[key] for key in ("read", "unreadable")}, {key: now[key] for key in ("read", "unreadable")},
            "page_record's fields changed: bump CACHE_VERSION in web/tagpup/cache.js and regenerate the fixture "
            "(python tests/test_page_record_fields.py --write)")
        self.assertEqual(now["version"], kept["version"],
                         "CACHE_VERSION moved: regenerate the fixture (python tests/test_page_record_fields.py --write)")


if __name__ == "__main__":
    if "--write" in sys.argv:
        with open(FIXTURE, "w", encoding="utf-8", newline="\n") as out:
            out.write(json.dumps(made_now(), indent=2, sort_keys=True) + "\n")
    else:
        unittest.main()
