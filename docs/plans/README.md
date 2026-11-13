# Plans for features across two domains

A feature that touches two domains has a short plan here before any worker starts (DECISIONS 2026-11-13, the retro
amended; #2925). Convert was briefed as two halves the same morning, and a script and a service were built for one
mechanism. Each worker launched for the feature cites the plan as a line `Plan: docs/plans/<name>.md`;
`.claude/hooks/plan-first.mjs` refuses a `tagpup-worker` launch naming two `Domain:` lines, or the integration owner,
without one. The plan is written by the main session with the owners of the domains it touches, and committed before
the first launch. It is a page, not a design log: what it decides, not how it was argued.

Copy this outline into `docs/plans/<feature>.md`:

```
# <Feature>: <one sentence: what the owner can do afterwards>
Live count: <rows or cases in the three libraries, counted read-only, with the date>
Domains: <the owners it touches>; integration reviews the seams

| Mechanism | The ONE place it lives (module, function) | Its owner | New or existing |
| <e.g. write a clip's tags> | <tagpup/services/...> | <Files> | <existing: reuse> |

Steady-state writer: a photo or clip added or changed afterwards: which writer keeps it right, with no command run.
One-offs: each script (or CLI command) run once, the service it calls (never its own copy of the mechanism), its dry
  run, and the count at which it is deleted (its dry run says 0 left).
Cuts: the merges in order, each worker's brief (one domain each), each within the branch budget.
```

A row with two places for one mechanism, or a one-off that does the work itself, is the plan's defect: fix the plan
before any worker starts.
