# Domain owners

Five owners, each a read-only agent with a charter in `.claude/agents/`: scope, the contract other domains
rely on, the invariants and their guard tests, a review checklist, and what the owner decides against what
is the human owner's. The code map is [ARCHITECTURE.md](ARCHITECTURE.md); the rules are
[INVARIANTS.md](INVARIANTS.md); the human owner's decisions are [DECISIONS.md](DECISIONS.md). A charter's file
paths are checked by `tests/test_owner_charters_name_real_paths.py`: a commit that moves a module changes its charter.

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
