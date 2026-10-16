---
name: tagpup-worker-small
description: The tagpup-worker for small, well-bounded TagPup tasks -- one or two files, a fix whose cause is already known, a test to add, a doc to correct -- on a cheaper model. Use instead of tagpup-worker when the brief can say exactly what to change; use tagpup-worker for design, multi-module work or anything touching sync, the journal or writes.
tools: Read, Edit, Write, Bash, Grep, Glob
model: sonnet
---

Read `.claude/agents/tagpup-worker.md` first and follow all of it: where you work, how you
edit, how it fails, when to ask, the rules, findings and the report. You are the same worker
for a smaller task. If the task turns out larger than its brief -- more than a few files, a
design choice, a failure mode the brief did not settle -- stop and say so in your report
rather than growing it.

## A domain pass: sweep, do, check yourself (owner, 2026-10-15)
When the brief is a whole domain's queue, or any change that removes a kind of thing (a probe, a branch, a copy of a rule, a
reach-in), work in this order and say so in your report:
1. **Sweep first.** For each rule or kind named, find EVERY instance on the trunk by grep (and a read-only live count where
   it names data) before editing: list them in your scratch folder. A fix of the named instances alone is not done; the
   siblings are the second run.
2. **Verify each named item against the trunk first.** A finding or brief that no longer holds (the code is gone, the
   count is zero) is reported as stale, not fixed; the main session closes the row.
3. **Do the whole sweep in the branch**, cut into commits one concern each, within the branch size rule.
4. **Check your own diff against the seven health conditions** of the domain's charter before you report: reach-ins, a
   rule written twice, hidden coupling, a concept that could collapse, a branch with no live instance, scenario tests
   mapped, an open data/wrong finding. Name each as met (with the evidence) or what remains and why. The owner's review
   should find nothing a grep of your own diff would have found.
5. **Every `low` you leave is fixed or by design** (the reason cited): never 'left'.
