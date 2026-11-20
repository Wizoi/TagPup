# Advanced filtering: the owner narrows the library by people, tags, folders and dates together, with AND and NOT

Status: DECIDED 2026-11-19 by the owner (answers at the end). Cut 1 may be briefed.
Live count: not applicable, it is a read-only view over existing rows and writes nothing.
Domains: Views/search/AI (owner of all of it); Integration reviews the page/route seam. No Data, Files, People or Media change.

## What exists today (surveyed 2026-11-19)

- The server already does AND, NOT and OR across all four facets: `kind="search"` with `all_of`, `any_of`, `none_of`, `words`
  (`store/library_view.py` `_search_scope`; tests `tests/test_library_search.py`). A union placed in `all_of` counts as ONE
  member, so "(folder A or B) AND (person X) AND NOT (tag Y)" is expressible with no server change.
- The navigator never produces it. It builds only a union (`any_of` of its selected rows) (`navigator-model.js`
  `specOfMembers`). Ctrl-click = OR.
- The search page (`search.js`) builds all three lists, but its pickers offer tags and people only; folders and dates reach
  it only through "Within" (the navigator's current union goes in `all_of`).
- The navigator's counts are whole-library. They are not narrowed by what else is selected.

So the feature is mostly a front end over an existing server capability, plus one new server piece (narrowed counts).

## How other tools do it (from general knowledge of the products; not re-verified online this session)

| Tool | Model | Lesson |
| --- | --- | --- |
| Windows Live Photo Gallery | Click a tag in the side pane to filter; the pane stays the filter. Combining is thin and implicit. | The pane IS the filter; it is simple because there is one gesture. It is also what the owner finds too limited. |
| Lightroom Classic (Library Filter bar) | Columns for Text / Attribute / Metadata (keyword, date, camera...). Within a column pick several values (OR); across columns they AND. | Cross-facet AND, within-facet OR is the model people already understand. The bar is toggled, so the plain browse view stays clean. |
| Apple Photos / Smart Albums, digiKam Advanced Search | A list of rules with "match ALL / ANY", each rule can be "is not". | Powerful, but a rule builder is a separate screen. Right for SAVED searches, heavy for everyday narrowing. |
| Faceted shopping and photo search (Google Photos, Amazon, Flickr) | Chips for the active filters; each facet's counts update to what remains; a chip can be excluded. | Narrowed counts stop dead-ends ("0 photos"). Chips make the current state visible and removable in one click. |

The shared answer: **OR within a facet, AND across facets, and an explicit exclude per item**, shown as chips, with a mode
switch so browsing is not cluttered.

## Proposal: a Filter mode on the navigator we already have

No new page and no new sidebar. The navigator keeps its four tabs. A "Filter" toggle (off by default) sits in its header.

### Off (today, unchanged)
Click goes to a place. Ctrl-click adds a place (OR). Screens look exactly as now.

### On
```
+-- Navigator ---------------------+   +-- Library ------------------------------------------+
| [Browse | (Filter on)]   Clear   |   | Showing 212 of 18,400                                 |
|                                  |   | [+ Rowan T.] [+ Ada L.] [& Beach] [- Screenshots]     |
| Folders Keywords People Dates    |   | [Folder: 2019, 2020] [Date: 2019]    x clear all      |
| -------------------------------- |   |-------------------------------------------------------|
| People                           |   |  [photo] [photo] [photo] [photo] ...                  |
|  + Rowan Thackeray        (212)  |   |                                                       |
|  + Ada Lindqvist          ( 96)  |   |                                                       |
|    Mina Oyelaran          ( 40)  |   |                                                       |
|  Keywords                        |   |                                                       |
|  & Beach                  ( 58)  |   |                                                       |
|  - Screenshots            (  0)  |   |                                                       |
+----------------------------------+   +-------------------------------------------------------+
   counts show what is left given the other filters
```
Each row in the panel has three states, one click cycles them, the marker shows which:

| Marker | Name | Meaning | Server shape |
| --- | --- | --- | --- |
| (none) | off | not used | nothing |
| `+` | any | photo has this OR another `+` of the same facet | one union per facet, put in `all_of` as ONE member |
| `&` | must | photo has this too | one `all_of` member of its own |
| `-` | not | photo does not have this | `none_of` |

Example from the owner: "Rowan AND Ada" is two `&` rows. "Rowan but NOT Ada" is `&` Rowan, `-` Ada. "Rowan or Ada" is two `+`.
Across facets AND is automatic: a `+` in People and a `+` in Dates give "(people any) AND (date any)", so the cross-section
filtering (folders then tags then dates, or people then tags then dates) is just clicking in more than one tab. The filter
chips above the grid list everything active across all tabs; clicking a chip removes it.

Why a mode and not a second sidebar: the same four tabs and the same tree, so there is nothing new to learn or to keep in step,
and the owner's "keeps it clean" is met because mode off is today's screen. Ctrl-click in Browse mode stays OR (no relearning).

Alternative the owner can pick instead (B): a separate "Advanced" search page, a rule list ("People / is / Rowan", "AND", "Tag /
is not / Screenshots"). Fits saved searches, costs a second place to look and loses the narrowed counts that make the panel
useful. Recommendation: A now, B only if saved searches are ever wanted (the data shape is the same, so A does not block B).

## What it takes in the current framework

| Mechanism | The ONE place it lives | Owner | New or existing |
| --- | --- | --- | --- |
| Evaluate AND / NOT / any across facets | `store/library_view.py` `_search_scope` | Views | existing, reused unchanged |
| Turn row states into a search spec and back | `web/tagpup/navigator-model.js` (beside `specOfMembers`), `library-source.js` `viewSpecFromSearch` | Views | new functions, one place; `membersOf`/`selectedRows` stop returning empty for `search` |
| Filter mode state and the chip strip | `state.js` `state.nav.filter`, a new module `web/tagpup/filter-bar.js`, wired in `main.js` | Views | new |
| Counts narrowed by the other filters | `store/library_view.py` `keyword_counts`, `people_counts`, `date_counts`, `folders` take an optional scope; route `/api/library/navigator?section=&within=` | Views | new, the only server change |
| Address round-trip (Back, bookmarks) | the spec is already in the address | Views | existing |

Everything stays in the Views domain. Rules to follow: a search spec is one `kind` carried through every layer
(`tests/test_view_rules_follow_the_server.py`), so no new view kind is added; the frontend reuses `kind=search`.
SQL rules from CLAUDE.md apply to the narrowed counts: no `LIKE`, no function on a column, check `EXPLAIN QUERY PLAN` on the
real library, and the person side stays the tag-tree node id.

## Cuts (each merge leaves the trunk whole, each under ~1,200 lines)

1. **Spec round trip + chip strip.** Filter mode on, tri-state rows, spec built into `kind=search`, chips above the grid,
   counts still whole-library. No server change. Scenario tests: rapid clicks, Back restores, two libraries, a deleted tag
   still in a bookmarked address.
2. **Narrowed counts.** Server counts under the current scope; a row that would give 0 shows dimmed, never hidden.
   Needs timing in a real browser on the big library before any speed claim (the `faces` and `photos` tables are large).
3. **Polish the owner asks for**: dates as a range as well as year/month, a words box in the same strip, a "save this filter".
   Only if wanted.

Steady-state writer: not applicable (read-only, writes nothing). One-offs: none.

## Cut 1 design as found in the code (2026-11-19; the first worker ran out of budget reading, so cut 1 is split)

Split: **1a** the model and its tests (pure, `navigator-model.js`, node tests, no DOM); **1b** the toggle, row markers, chip strip,
history and leaving the filter. 1b starts after 1a merges.

- Server needs no change: `_search_of` keeps an `any_of` inside `all_of` as one member; a union of one is its member.
- **Spec from rows:** each facet with `+` rows becomes one `{kind:'any_of', value:[...]}` in `all_of`, even for one row (so a lone
  `+` and a lone `&` differ and Back restores the right marker); each `&` row is its own `all_of` member; `-` rows go in
  `none_of`; `any_of:[]`, no words. Beside `specOfMembers`.
- **Rows from spec:** a Map of member key -> `+`/`&`/`-`, or null when the search is not a filter. It is a filter when: no words,
  empty `any_of`, each union in `all_of` holds one facet only with at most one union per facet, every member is a kind a row can be
  (folder only with subfolders; `keyword` not `keyword_only`), no member in two states. (Within's cross-facet union is thus not a filter.)
- **A search made elsewhere** (words, Any-of list, Within): no marks, chip strip hidden; a click starts a new filter, pushed as a
  new place. The search page is unchanged.
- **History (1b):** first filter click from a non-filter view pushes a place (`searchBack`), later clicks replace; Back goes to the
  Browse view before. An emptied filter or Filter off with a filter open leaves through ONE shared function in `library-view.js`
  that `search.js clearSearch` also uses, with a `leaving` guard in `state.nav.filter` (the search box's Clear has the same race
  today: a `low` finding to file). Clicks cycle off, `+`, `&`, `-`, off; Ctrl-click sets `+`; a grouping row only opens/closes.
- **Chips:** deleted tags/people use the existing `chipKnown` (`search-model.js`).
- **What turns Filter mode on from an address:** DECIDED (a): remember the toggle in this browser (localStorage, as
  `details-panel.js` does), nothing in the address. No `filter=1` parameter (it would touch `VIEW_PARAMS` and every spec rebuild).

## Typed grammar (owner's addition, 2026-11-19): TABLED by the owner the same day; not part of cuts 1-2, no cut 3 until raised again

One text box that takes a query, with a `?` icon that opens the grammar with examples. It is the same filter as the chips (the
chips show what the text means; editing either updates the other) and it is the phone page's way in, since the phone needs no
tri-state tree.

```
rowan AND ada                      both people
rowan AND (ada OR mina) NOT beach  rowan, with ada or mina, but not beach
person:rowan year:2019 -screenshots   typed prefixes, minus as NOT
```

Grammar: `AND` (also a space), `OR`, `NOT` or a leading `-`, parentheses, `"quoted phrases"`, an optional `kind:` prefix
(`person:` `tag:` `folder:` `year:` `month:`; a bare word with none searches the words as today and offers the matches).

- **ONE parser, on the server** (Views owner, `tagpup/services/`, e.g. `library_query.parse`), never a copy in the desktop page and
  another in the phone page. A route returns the spec plus how each word was understood (`rowan` -> person Rowan Thackeray) or
  that it was ambiguous or unknown, so both pages show chips and a clear "no tag called x" instead of an empty grid.
- **It builds the same `kind=search` spec**, so no new view kind and no new SQL.
- **Limit of the model, to say in the `?` text:** the server's search is "all of these, any of those, none of these". It can
  express `A AND (B OR C) NOT D`, but not an OR of ANDs, `(A AND B) OR (C AND D)`. Supporting that is a server change (a nested
  expression tree in `_search_scope`) and a larger one-way choice; recommended to refuse it with a plain message now, and
  build the tree only if the owner hits a real need.
- Autocomplete for names while typing (people, tags, folders) uses the vocabulary already loaded; failures stay on the status line.

Cut order: the grammar is cut 3, after the chips (cut 1) and the narrowed counts (cut 2); the parser needs the chip strip to
show its result. It replaces the "words box" idea. Questions:
1. Is "refuse an OR of ANDs for now" acceptable, or do you want the nested tree from the start?
2. A bare word: search words as today (recommended), or look for a person/tag of that name first and ask?
3. The phone page gets the text box first, before it gets Filter mode chips? (recommended: yes, the box is the phone's whole filter UI)

## Owner's answers (2026-11-19)

1. A: the Filter toggle on the navigator.
2. Ctrl-click in Filter mode makes a row `+` (any of).
3. A folder row filters like Browse does, including its subfolders.
4. Dates stay year and month rows; no from-to range.
5. Cut 1 ships before narrowed counts.
6. A filter stays temporary: no saved searches. Cut 3 shrinks to the optional words box.
