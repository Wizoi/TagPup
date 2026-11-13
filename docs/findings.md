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
| 2944 | 2026-11-13 | low: the read-budget hook counts lines, not bytes: a text file under 300 lines with very long lines (a saved tool result of one JSON line) is let through whole; the Read tool cuts each line at 2,000 characters. The brief asked for a line count; the retro's 18-26k saved tool-result reads were not measured for their line counts this task. | open |
| 2952 | 2026-11-13 | low: `dates._a_clips` is a third way to tell a clip from a photo beside `images.is_clip` and `photos.kind`; it agrees with `kind` on 2,920 of 2,920 live clips and 0 of 64,874 photos, and differs only on an unread row (live count 0). Should `store.photos._dated` pass the row's `kind` to `photo_year`? Media owner 2026-11-13. | open |
| 2954 | 2026-11-13 | low: `.claude/hooks/plan-first.mjs` names an owner by the first word of each part of a `Domain:` value (split on `and`, commas), so `Domain: Files / Media` or `Files & Media` counts one owner. Not matched: no brief of that shape is known; the split would add `/` and `&`. Found while building #2927-#2929 (tests added first, failing on the old hook). | open |
| 2955 | 2026-11-13 | low: decided, not copied: with the packet carry's no-room arm deleted (#2951), `field_values.packet_given` raises `Refused(images.CLIP_NOT_PATCHED)` when `in_place.patch_clip` places nothing, as `_clip_written` does, and convert's existing Refused arm says it; no ffmpeg-made copy reaches it (an MP4 of clip_convert is ftyp, moov, free, one sized mdat, and took an appended box, checked on a .wmv and an .mp4 2026-11-13). | open |
| 2956 | 2026-11-13 | low: `tests/owners.py` C5's scenario text still describes the deleted clip-dating one-off (dry run, --apply refused, wrote N), and C7's does not name a clip written by another program while Convert reads it (refused CHANGED, nothing planned; the test added for #2945). Outside this branch's files. | open |
| 2957 | 2026-11-13 | low: the clips a browser cannot play could not be counted this session: every clip of the four library files is on a share, and ffprobe (`video_frames.streams`) found the share not answering for all 2,920; the rows hold no codec (raw_metadata has no QuickTime:CompressorID). photo_index's split of change 8261 (620 appended, 49 in place) cannot be recounted either: the journal erases a file's patch record when it is done (669 files done, counted). | open |
| 2959 | 2026-11-13 | low: #2932's premise was wrong: the fourth `.db` in `data/` is `kr-track.before-people-merge-20261102-122703.db`, a backup, so the libraries are three; counted again here, photo_index holds 62,229 photos (not 59,319) and kr-track 2,186 (not 2,181), 2,910 and 5 clips; the Files and Views pages now say so. | open |
