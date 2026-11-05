# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-11-05 | low: `tagpup/files/clip_patch.py`'s docstring still says `xmp_packet.edit` refuses every live clip packet, its properties being attributes; since the editor reads and edits the attribute form, 79 of the 80 live clip packets edit (counted this session, in memory; the 80th is over `LIMIT`). clip_patch.py is another worker's file in this pass, so the sentence was left. | open |
| B-2 | 2026-11-05 | low: decided by the worker, not copied: a property `xmp_packet` finds written as an attribute is changed in the attribute (its quote kept; a tab, CR, LF, `&`, `<`, `>` and the quote written as references) and cleared by removing the attribute with the whitespace before it; it is never converted to an element. A cleared attribute set again is written as an element, so an undo of a clear gives the values back, not the bytes. Only a text property is read in attribute form: a Bag or an Alt as an attribute is refused (none in the 80 clip packets or the 4,673 JPEG packets read). | decided: the smallest change that keeps every other byte; the journal compares values (tests/test_xmp_packet_edits_what_it_owns.py, AClipsPacketWithItsPropertiesAsAttributes) |
| B-3 | 2026-11-05 | low: the 39 bare "Adobe XMP Core 5.1.0-jc003" clip packets have no wrapper and so no padding: every edit but one of the same length (a date of the same spelling) needs `grow=True`, which makes the clip's patch a move or the rewrite rather than an overwrite. Semantics unchanged by this branch; for M7's wiring to know (counted this session in memory: 39 of 39 refuse a keyword without grow, 39 of 39 edit with it). | open: for the M7 brief |
