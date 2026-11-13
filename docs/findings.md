# Findings

This file records every review finding, bug report and known problem: what was found, and where it stands. A finding that exists only in a conversation gets lost, and a decision that is not written down gets made again.

**This file holds the open findings.** A closed row (status starting `fixed`, `built`, `decided`, `closed`,
`accepted`, ...) is moved, byte for byte and with its number, to
`history/findings_closed_0001-0250.md` and so on ([index](history/README.md)) -- one file per 250 numbers.
A reference such as "#726" is found by its number there. The owner's decisions are collected in
[DECISIONS.md](DECISIONS.md); the rules that came out of findings are in [INVARIANTS.md](INVARIANTS.md).

- **Add a row when something is found**, before fixing it.
- **Severity** *(owner, 2026-10-09; CLAUDE.md, "Keeping the project small")*: start the Finding with `data:` (can lose or
  corrupt data), `wrong:` (a wrong result the owner sees) or `low:`. Only `data` and `wrong` block a merge; `low` rows are
  batched. Older rows carry none.
- **Status** is one of:
  - `open`
  - `fixed`: give the commit, by its hash or, for a row added in the same commit, its subject.
  - `not reproduced`: say what was tried.
  - `left as is`: say why.
  - `tabled`: say until when.
  - `planned`: give the phase in [ARCHITECTURE.md](ARCHITECTURE.md).
  - `watching`: nothing to do until it is reported again or reproduced *(owner, 2026-11-02)*. The row is moved by
    `tools/add_findings.py --watch N "why"` to [watching.md](watching.md): not counted open, not brought up in a report;
    **read watching.md when an issue is reported**, to see whether it is one of those. A reproduced one goes back here by hand.
- **Decisions the owner makes** are marked *(owner, date)*. Don't reopen them without a new fact.
- **Never put real names here.** The library is photographs of real people.
- **Numbering is the main session's.** A worker does not number rows into this file (two branches that both do collide on
  merge). It writes `| ? |` rows with `tools/add_findings.py rows.md --branch-file docs/findings_pending/<branch>.md`
  (provisional `B-1`, `B-2`; never write a `B-n` into code or a commit message) and the main session, merging the branch,
  runs `tools/add_findings.py --take docs/findings_pending/<branch>.md`, which numbers the rows after the highest number
  in this file **and the archives**, and removes the file. `tools/add_findings.py --status N "fixed: abc"` sets a status
  (in the archive if the row is there); `--archive` moves the rows whose status closed.

Findings fixed before 2026-09-23 are in the git log (`git log --grep="^fix"`). This file starts with what was still open or decided on that day.

| # | Found | Finding | Status |
|---|---|---|---|
| 2924 | 2026-11-13 | low: `tools/add_findings.py` `similar_rows` only prints a notice when a new row resembles an earlier row closed `fixed` (#2908 and #2918 share clip_patch/in_place after #2893), so a third fix for one symptom is not stopped. Retro 2026-11-13 action 2: it refuses unless the new row says "second fix of #n: the single place is ...". Metric: chains of three fixes for one symptom 1 now, target 0. | open |
| 2930 | 2026-11-13 | low: the Media domain's first page (docs/domains/media.md) states conditions 3 (no hidden coupling: a clip's patch, settle and read-back held together by test) and 5 (Convert's old-format arm, 0 clips of .wmv/.mpg/.avi left in any library, counted 2026-11-13) as not yet stated: the owner's first pass decides each, and counts the clips holding an XMP packet and those a browser cannot play (a TODO on the page). | open |
| 2931 | 2026-11-13 | low: decided in the carve, not copied from the brief: contract row 69's caller became M (the viewer is `web/tagpup/media.js`, now Media's), the Views charter's `/api/video-file` Scope entry kept but marked as Media's scenario C8, and `tests/test_container_layout.py` went to Media though `tagpup/files/containers.py` stays Files' (the brief's choice). | open |
| 2932 | 2026-11-13 | low: `data/` holds four library files, not the three the charters and domain pages name: a fourth of 2,065 photos and 5 clips beside kr-track (2,181 and 5), photo_index (59,319 and 2,910) and renton_parkrun (1,309 and 0), counted read-only 2026-11-13; photo_index's clips were 2,179 on 2026-11-08, so the Files and Views pages' 2026-11-08 counts are stale. | open |
| 2937 | 2026-11-13 | low: docs/domains/media.md does not state the clip-dating rule (file name only, at midnight) or the one-off with its counts (669 of 680; 620 appended, 49 in place, 0 unplaceable; counted 2026-11-13). | open |
| 2944 | 2026-11-13 | low: the read-budget hook counts lines, not bytes: a text file under 300 lines with very long lines (a saved tool result of one JSON line) is let through whole; the Read tool cuts each line at 2,000 characters. The brief asked for a line count; the retro's 18-26k saved tool-result reads were not measured for their line counts this task. | open |
| 2949 | 2026-11-13 | low: no integration contract row for Media's new reach into Files (`convert._ready` and `_packet_carried` call `in_place.packet_held`, `in_place.UNREAD`, `field_values.packet_given`); the Files domain doc still says a clip patch is outside `field_values.write`. Reviewer 2026-11-13. | open |
| 2952 | 2026-11-13 | low: `dates._a_clips` is a third way to tell a clip from a photo beside `images.is_clip` and `photos.kind`; it agrees with `kind` on 2,920 of 2,920 live clips and 0 of 64,874 photos, and differs only on an unread row (live count 0). Should `store.photos._dated` pass the row's `kind` to `photo_year`? Media owner 2026-11-13. | open |
| 2954 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` names an owner by the first word of each part of a `Domain:` value (split on `and`, commas), so `Domain: Files / Media` or `Files & Media` counts one owner. Not matched: no brief of that shape is known; the split would add `/` and `&`. Found while building #2927-#2929 (tests added first, failing on the old hook). | open |
| 2955 | 2026-11-13 | low: decided, not copied: with the packet carry's no-room arm deleted (#2951), `field_values.packet_given` raises `Refused(images.CLIP_NOT_PATCHED)` when `in_place.patch_clip` places nothing, as `_clip_written` does, and convert's existing Refused arm says it; no ffmpeg-made copy reaches it (an MP4 of clip_convert is ftyp, moov, free, one sized mdat, and took an appended box, checked on a .wmv and an .mp4 2026-11-13). | open |
| 2956 | 2026-11-13 | low: `tests/owners.py` C5's scenario text still describes the deleted clip-dating one-off (dry run, --apply refused, wrote N), and C7's does not name a clip written by another program while Convert reads it (refused CHANGED, nothing planned; the test added for #2945). Outside this branch's files. | open |
| 2957 | 2026-11-13 | low: the clips a browser cannot play could not be counted this session: every clip of the four library files is on a share, and ffprobe (`video_frames.streams`) found the share not answering for all 2,920; the rows hold no codec (raw_metadata has no QuickTime:CompressorID). photo_index's split of change 8261 (620 appended, 49 in place) cannot be recounted either: the journal erases a file's patch record when it is done (669 files done, counted). | open |
