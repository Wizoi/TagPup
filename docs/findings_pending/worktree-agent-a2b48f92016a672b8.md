# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-17 | data: own writer (field_values.write, PATCH_IN_PLACE; file_changes.removed_scratch rule 3): a patch in place never looks for ExifTool's scratch file beside the photo, which is the one sign (DECISIONS 2026-10-29) that another process's rewrite of the photo (the CLI, a second library holding the folder) is under way; that rewrite read the photo before the patch and its os.replace (field_values._landed, which compares only the picture) lands over it, so the file loses the patched tag or caption while this process's journal and row record it held. Unreproduced (reasoned from the code); shown only as far as tests/test_exiftool_says_why.py's bulk write over a scratch written this moment answering 200 under the patch (500 under the rewrite). Not new in 4d (ExifTool's header-copy patch skipped the scratch too), but before 4d a keyword add fitted that patch (which wrote IPTC's copy too) in 4 of 1,461 sampled photos (DECISIONS 2026-11-03) and was otherwise a rewrite, which the scratch rule guards; now nearly every keyword or caption save is a patch. user action: a CLI bulk tag write, or a second library's save, of a photo while the page saves it. | open |
