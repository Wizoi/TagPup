# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-12 | low: tagpup/files/clip_convert.py:278 `wait = VERIFY_SECONDS + os.path.getsize(temp) / READ_RATE` is a look at a file on the photo's share outside tagpup.files.shares, unbounded and with no `# unbounded:` reason, added by eb9133e0 (the flush's bound grown with the copy): tests/test_photo_looks_have_one_owner.py fails on the branch (`tagpup\files\clip_convert.py:278 os.path.getsize`; run 2026-11-12), so the commit gate does not pass. The scenario it guards: a share that stops answering just as ffmpeg ends holds the conversion's thread, and the batch line behind it, in getsize for as long as Windows waits, before the bounded flush is reached. The size is the copy's, read once: shares.stamp_of(temp) (bounded; UNANSWERED or None is NOT_FLUSHED) gives it. | fixed: d55a3231 |
