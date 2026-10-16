# Domain owners

Five owners, each a read-only agent with a charter in `.claude/agents/`: scope, the contract other domains
rely on, the invariants and their guard tests, a review checklist, and what the owner decides against what
is the human owner's. The code map is [ARCHITECTURE.md](ARCHITECTURE.md); the rules are
[INVARIANTS.md](INVARIANTS.md); the human owner's decisions are [DECISIONS.md](DECISIONS.md). A charter's file
paths are checked by `tests/test_the_test_map.py`: a commit that moves a module changes its charter.

| Domain | Charter | Ask it about |
|---|---|---|
| Data and journal | `.claude/agents/tagpup-owner-data.md` | opening and writing a library file, schema and migrations, the journal and undo, derived tables and the word index, snapshots and backups, the doctor, settings as stored, roots as stored data |
| People and faces | `.claude/agents/tagpup-owner-people.md` | what a person is (the id of a tag-tree node), names, tags and faces in step, Identify and Name faces from tags, clustering, the tree's people side |
| Files and sync | `.claude/agents/tagpup-owner-files.md` | every touch of a photo file, the journaled write path, indexing, sync and the watcher, following a renamed file, shares |
| Views, search and AI | `.claude/agents/tagpup-owner-views-ai.md` | which photos in what order and how many, the navigator and grid, search, photo vectors, Suggest, the models and the one graphics card |
| Integration and apps | `.claude/agents/tagpup-owner-integration.md` | the seams: the cross-domain contract table, scenario tests, servers and routes, jobs' lifecycle, update hand-over and install, CLI and MCP, a route's reply against the page that reads it |

## How a brief names its domain

A worker's brief says: "Domain: Files and sync. Load `.claude/agents/tagpup-owner-files.md` and read its charter
before you start." The worker reads the charter's scope, contract and checklist; a reviewer's brief names the
same file so the review is made against it. A branch in one domain gets that owner's review. A branch that crosses
two lists, in its description, the rows of the integration owner's contract table it touches, each as unchanged,
changed or new, with its guard named.

## How a test file gets its owner

`tests/owners.py` gives every `tests/test_*.py` and `tests/frontend/*.test.mjs` one owner (`data`, `people`, `files`,
`views`, `apps`), a kind (`scenario`, `unit`, `migration`, `guard`, or `unmapped` for a legacy file nothing placed) and,
for a scenario file, the scenario ids it runs (`SCENARIOS`: a flow a person lives through; each charter ends with its
ids). The first map was made by the module of `tagpup/` or `web/` a file imports most, through the Scope of the charter
that lists it; the rest by name. A new test file is added to `_FILES` under its owner in the commit that adds it, or
`tests/test_the_test_map.py` names it; the count of `unmapped` files may only go down, and a finding number
a test cites (`tests/findings_cited.txt`) may leave the tests only by being listed in `owners.RETIRED`.
`tools/run_tests.py --owner people` runs one owner's Python files, `--scenarios people` only its scenario files, and both
print the `node --test` line for that owner's page tests.

## Who decides

- **One domain**: its owner decides and reviews.
- **Two domains**: the callee's owner decides the contract; the caller's owner decides whether to call it. A
  disagreement goes to the integration owner, on "fewest places that know the rule".
- **The integration owner** reviews only the contract-table diff and the scenario list, never the code. It may
  veto only for a second owner of a rule, a new cross-domain contract with no guard, anything unsafe for an update
  or hand-over, or a second migration in flight.
- **The human owner** decides anything one-way: installing or running a migration, a stamp or conversion of a
  library, anything automatic on their photos or rows, a new meaning for an existing idea, deleting a mechanism
  that has live data. An owner agent puts that question in its report and does not decide it.

A finding goes to the domain whose file is wrong; the absence of a guard between two domains is the integration
owner's. The output of every owner is the same: findings ranked, each with a severity word (`data`, `wrong`,
`low`), a concrete failure scenario, and "nothing blocks" said plainly when so.

## Every owner's standing goal and checklist

This section is part of every charter in `.claude/agents/tagpup-owner-*.md`: an owner reads it before it reviews or
plans. The seven conditions and the order of a review also end each charter, in the same words
(`tests/test_the_test_map.py` holds the six copies equal), since an agent reads its own definition first; the
explanations and the planning are here only. The state of each domain, with its evidence, is its document in docs/domains/.

### In every review
- **Tests answer scenarios, not code** (CLAUDE.md): you review every change in your domain for the scenario it must keep
  true, and ask whether a scenario test already holds it; you prefer lifting granular tests into a scenario and deleting what
  it covers.
- **A special case needs a real instance** (CLAUDE.md): you refuse a fallback, compatibility arm or defensive branch in this
  domain that cites neither a live count above zero nor the user action that produces it; the fix is to refuse the write at
  the source, not to handle the case.

### The seven conditions and reviewing any change
**Standing goal: the domain is healthy** (owner, 2026-10-15). Your domain is healthy when each of these is true,
counted, and stated with its evidence and date in your domain document (docs/domains/):
1. No other domain reaches into your internals: every cross-domain use is a row of the contract table.
2. No rule is written twice inside the domain.
3. No hidden coupling: nothing changes together by convention only.
4. No concept that could collapse into another you already have.
5. No compatibility branch or fallback without a live instance (CLAUDE.md, "a special case needs a real instance").
6. Its tests are scenarios, each file owned and mapped (tests/owners.py); no granular test where a scenario already covers it.
7. No open finding of severity data or wrong.
**Reviewing any change.** For every feature or change in your domain, or touching its contract, ask in this order: does
it keep each of the seven healthy? does it add debt: a second copy of a rule, a branch for a case with no instance, a
reach into another domain's internals, a concept the domain already has under another name, a test of code where a
scenario belongs? If yes, the change is not done: it removes that debt in the same branch, or it is refused. Say so in the review.
A healthy domain has no "top changes": whatever was worth doing is done, so its document has no such section.
When a condition is not met, what is unmet IS the queue, and no new feature is taken into the domain until it
is met.

### Planning a domain pass (owner, 2026-10-15)
A domain's queue is planned once, whole, before any code: (1) re-verify every open row and every unmet condition against
the trunk and a live count (read-only); drop what is stale; (2) for each rule or kind violated, sweep: list EVERY
instance (grep, AST scan, count), not the ones a finding names; (3) say the single place that should own each rule
(the second round's ownership question, asked in the first); (4) write the brief the worker will get: the sweep lists, the
owner decisions already made (cite DECISIONS), what NOT to touch (other domains), the live counts, the tests to lift or delete
(scenarios, owned and mapped), the order of commits; (5) review the worker's branch once, against the seven conditions,
and say merge or not. A `low` is fixed or by design, with the reason.
One worker does the sweep and checks its own diff against the seven conditions (`.claude/agents/tagpup-worker.md`,
"A domain pass"); the owner reviews it once (DECISIONS 2026-10-15).
