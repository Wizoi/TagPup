# Phone redesign: the phone page looks like a modern photo app, filters by people, keywords, folders and dates together, and shows a photo's facts cleanly

**Status: a plan for the owner to approve (the mock-ups below), nothing built.** Written 2026-11-23 at the owner's request.
Builds on [phone-viewer.md](phone-viewer.md) (the page, built and installed) and [advanced-filtering.md](advanced-filtering.md)
(Filter mode, built on the desktop). Plan only, no code changed.
Live count: not a data feature: it writes nothing new (the Edit sheet keeps its two existing routes). What it reads: the
existing library routes, plus one new read-only route (covers, cut 2). A count of rows or cases is not applicable; the
numbers that matter are the page's own, counted from the code 2026-11-23 (next section). Rows a browse level holds (the
largest, a folder level or the keywords) are to be counted read-only before cut 2, not assumed; the cover route is built
for the visible rows only, so the count sets a test size, not a design.
Domains: Views (every cut: the page, the filter model reuse, the cover read); Integration (the new route and its contract
row, the harness and spec tests). Files, Data, People, Media: no change (the Edit sheet's writes are untouched; a person's
row is the tag-tree node, read as the desktop reads it). Integration reviews the seams.

## What exists today (counted 2026-11-23)

| File | Lines | What it is |
|---|---|---|
| `web/tagpup/mobile.html` | 111 | the page: top bar (back, title, Libraries), five `data-screen` sections (browse, grid, photo, library) and the edit sheet |
| `web/tagpup/mobile.css` | 464 | one stylesheet, about 70 rules; plain buttons, a bordered list, no tab bar, no cards, no motion, one breakpoint-free column |
| `web/common/theme.css` | 26 | 14 colour tokens, two fonts, one shadow; no spacing, radius, type or motion tokens; no light theme |
| `mobile-browse.js` | 191 | four big text buttons (Folders, Keywords, People, Dates), then a drill-down list of text rows with a count |
| `mobile-grid.js` | 156 | the grid (`createVGrid`), two `<select>`s (Order, Show), a count line |
| `mobile-photo.js` | 206 | the large picture, swipe, zoom; under it only the file name and one grey line (`photo-at`) and an Edit button |
| `mobile-edit.js` | 385 | the edit sheet: people and keyword chips, add box, Title, Date Taken, Save |
| `mobile-screens.js`, `-state.js`, `-library.js`, `mobile.js` | 147, 51, 56, 41 | history-step screens, state, library list, wiring |

Total 1,834 lines for the page (including theme.css). What looks weak, from the code: there is no navigation except a
back arrow and a "Libraries" text button; Browse is four text buttons and a list of words and numbers, so a person or a
folder is a line of text where the photographs are the whole point; the grid has two raw `<select>` controls and no
filter at all (the phone cannot say "Rowan and Ada, not Screenshots"; it only has words search); the photo screen shows
a file name and a date line, with the keywords and people hidden behind Edit; controls are 44 px but undifferentiated
(one visual weight for every button); no motion, no sheet, no skeleton while thumbnails load.

What it can reuse unchanged: `navigator-model.js` (`indexSection`, `rowTree`, `sectionRows`, `specOfMembers`, and the filter
model `specFromRowStates` / `rowStatesFromSpec`, `filterKey`, `FILTER_PARTS`), `library-source.js` (`newLibrary`,
`viewSpecFromSearch`, `viewCountSaid`), `search-model.js` (`chipKnown`, `chipLabel`), `vgrid.js`, `image-zoom.js`, `swipe.js`.
The server already does AND, ANY and NOT across facets (`store/library_view.py` `_search_scope`; advanced-filtering.md), so
the phone sends the same `kind=search` spec the desktop does: no new view kind, no new SQL for the filter.

## What other photo apps do, and what is adopted

Research 2026-11-23 (WebSearch; the results were news coverage of the products, not design specs, so the product claims
below are what those articles say, and the numeric rules are the platform's published guidance and the existing plan's;
Lightroom mobile and PhotoPrism returned no usable source this session and are taken from general knowledge, marked so).

| Pattern seen | Where | Adopted here, and why |
|---|---|---|
| Bottom navigation of three to five equal-weight destinations; Google moved Photos to a floating pill with a separate search button; Material 3 asks that bar items be of equal importance | Google Photos, Material 3 (9to5google, Android Authority coverage) | **A bottom tab bar of four: Library (browse), Photos (the grid), Search/Filter, Libraries.** The thumb reaches it on a tall phone; the top bar keeps only the title and context. Plain docked bar, not floating: floating pills cover the last grid row and Android Back must still go up one screen (phone-viewer.md). |
| Chip types: filter chips toggle options in context; chips show the active state and remove in one tap | Material 3; Immich search chips (v1.118 "quick access chips", v2.4 search-type pill) | **A sticky horizontal chip row above the grid** showing each active filter (`+ any of`, `& must`, `- not` marks as on the desktop) with an x, and a "Filter" chip that opens the sheet. It is the desktop's chip strip, drawn for a thumb; the marks mean the same. |
| One filter menu rather than many (iOS 18 condensed filters into a single menu); one scroll for the library, collections below | Apple Photos iOS 18 (MacRumors, TechCrunch coverage) | **One Filter bottom sheet**, four tabs (People, Keywords, Folders, Dates) in the same order as Browse, rather than four separate screens. The chip row is the summary, the sheet is the editor. A bottom sheet, because the grid and its count stay visible behind it and the result updates live. |
| Cover thumbnails for collections (people, albums, places) so a collection is a picture | Google Photos, Apple Photos, Immich, PhotoPrism (general knowledge) | **Browse rows get a rounded thumbnail and a count** (owner's decision 2). |
| Dense, edge-to-edge square grid with a small gutter; large photo view on a dark canvas with the facts below or in a pull-up sheet | all of the above | The grid stays (owner's decision 2). Photo view: picture on the dark canvas, facts in a pull-up bottom sheet whose first third shows Date Taken, title and chips without opening it (owner's decision 3). |
| Pinterest-style staggered columns | Pinterest | **Not adopted.** It needs each photo's aspect ratio before layout and breaks `createVGrid`'s windowing, which keeps a 60,000-photo view cheap. Square cells with `object-fit: cover` keep the model; the owner said the grid stays. |
| Skeleton placeholders, reserved image space (no layout shift) | Immich (incremental load), the web.dev guidance on layout shift | Every thumbnail cell and browse thumbnail has its box and a tonal placeholder before the image arrives, so a slow link shows structure, not a jump. |

The concrete rules adopted (all in `theme.css` as tokens, so the desktop page can adopt them later, not now):

- **Tab bar**: 56 px plus the bottom safe-area inset (`env(safe-area-inset-bottom)`), four items each at least 44 x 44 px
  (the project's existing target), icon over a 12 px label, the active one tinted `--accent` with a pill behind the icon.
- **Radius and elevation**: three radius tokens (8 px chips and inputs, 16 px cards and thumbnails, 28 px sheets); cards are
  distinguished by tone (`--bg-tertiary` over `--bg-secondary`), not by heavy shadow, which is invisible on near-black.
- **Type scale**: five steps in `rem` (so Android's font scale still applies, a standing decision): 0.75 caption, 0.875 body
  small, 1 body, 1.25 title, 1.75 display; weights 400, 600 only; the existing fonts (`--font-heading` for titles,
  `--font-body` for the rest). Inputs stay 16 px+ so no zoom.
- **Dark theme tokens**: keep the existing slate scale (`--bg-secondary`, `--bg-tertiary` are the first two tonal steps above
  `--bg-primary`, used by those names); add `--surface-3` (the third),
  `--scrim` (rgba black 0.6 under a mark drawn over a thumbnail; the Edit sheet has no scrim, so the photo stays
  visible above it; the Filter sheet, cut 3, decides its own), `--on-accent`, `--danger`, and a photo-canvas `#000` for the photo screen so the
  picture, not the chrome, is brightest. Contrast checked 4.5:1 for text on each surface. No light theme (the app has none;
  not reopened).
- **Motion**: 150-250 ms, ease-out, for a sheet rising, a chip appearing and a tab change; `transform` and `opacity` only;
  all of it off under `prefers-reduced-motion`.
- **Safe areas and targets**: `viewport-fit=cover` already set; top bar pads `env(safe-area-inset-top)`, the tab bar and sheets
  pad the bottom inset, the photo screen the left and right insets in landscape. 44 px minimum for every tap target; chips are
  36 px high with a 44 px hit area (padding on the button).
- **Sticky chip row**: `position: sticky; top: <top bar>`; it scrolls away with the top bar only on the photo screen.

## The three screens, for the owner to approve

Phone portrait, about 390 px wide. `[ ]` is a button, `( )` a count, `###` a thumbnail.

```
BROWSE (tab: Library)                   GRID with filter chips (tab: Photos)
+-------------------------------+       +-------------------------------+
| TagPup                  [lib] |       | Photos           212 photos   |
| [ Search words           (o)] |       | [Filter 3] [+Rowan x][+Ada x] |  <- sticky, scrolls sideways
|                               |       | [&Beach x] [-Screenshots x]   |
| People                  See all       | [Newest v] [Photos v]         |
| (###) (###) (###) (###) ->    |       |-------------------------------|
|  Rowan  Ada   Mina   Zoe      |       | ### | ### | ### |             |
|   212    96    40     31      |       | ### | ### | ### |             |
|                               |       | ### | ### | ### |             |
| Folders                       |       | ### | ### | ### |             |
| +---------------------------+ |       | ### | ### | ### |             |
| | ### 2019            1,204 > |       |                               |
| | ### 2020              988 > |       |   zero results shows:         |
| | ### Holidays          402 > |       |   "No photo has all of these. |
| +---------------------------+ |       |    [Remove Screenshots]       |
| Keywords                      |       |    [Clear all]"               |
| +---------------------------+ |       |                               |
| | ### Beach              58 > |       |                               |
| | ### Birthday           41 > |       |                               |
| +---------------------------+ |       +-------------------------------+
| Dates                         |       | [Library] [Photos] [Filter] [..]
+-------------------------------+       +-------------------------------+
| [Library] [Photos] [Filter] [Libs]|      Filter sheet (bottom, 28 px radius, over a scrim):
+-------------------------------+          [People|Keywords|Folders|Dates]   Clear
                                           (###) Rowan Thackeray   (212)  [ off | + | & | - ]
                                           (###) Ada Lindqvist     ( 96)
                                           [ Show 212 photos ]   <- live count on the button

PHOTO (no tab bar; black canvas)
+-------------------------------+
| <                        1/212|
|                               |
|        the photograph         |   swipe left/right, tap = zoom
|        (fills the width)      |
|                               |
|-------------------------------|  <- pull-up sheet, 28 px radius, handle
| Sat 14 June 2019, 3:42 pm     |  Date Taken, large
| Beach day with the cousins    |  title/caption, wraps to 3 lines then "more"
| People    (Rowan) (Ada)       |  chips
| Keywords  (Beach) (Summer) (Family/Holidays/Cornwall)
| [ Edit ]                      |  the existing edit sheet, unchanged
+-------------------------------+
```

Notes the owner can change before approval: the tab names; whether Photos is the grid or "Library" is the whole top-level
(Apple-style one scroll); a person's thumbnail a photograph (recommended, cheap) rather than a face crop (nicer, needs a
face-crop route that does not exist for the phone today, a People-owner question for a later cut).

## Mechanisms: the ONE place each lives

| Mechanism | The ONE place it lives | Owner | New or existing |
|---|---|---|---|
| Colours, radius, type, spacing, motion tokens | `web/common/theme.css` (tokens added to `:root`); `mobile.css` uses them, holds no literal colour or size twice | Views | existing file, extended |
| The tab bar and its destinations | `web/tagpup/mobile-tabs.js` (draws and wires; a tab is a screen of `mobile-screens.js`) and the `nav` in `mobile.html` | Views | new, ~120 lines |
| Screens as history steps, Back goes up | `mobile-screens.js` `openScreen` / `upAScreen` (a tab press is `openScreen`; Back from a tab's root leaves the page as today) | Views | existing, one screen added (Filter sheet as a sheet step like the Edit sheet's `openSheetStep`) |
| What a filter is: row states, the spec, the inverse | `navigator-model.js` `specFromRowStates`, `rowStatesFromSpec`, `filterKey`, `FILTER_PARTS` | Views | existing, imported as they are |
| Chip labels, a deleted tag still in an address | `search-model.js` `chipLabel`, `chipKnown`; `filter-bar.js` `markOf` text for the marks | Views | existing; `markOf` and `PART_SAID` are DOM-free and imported (if `filter-bar.js` cannot be imported without its DOM ids, the two pure functions move to `navigator-model.js`, moved not copied) |
| The phone's filter state | `mobile-state.js` `state.filter` (a Map filterKey -> `+`/`&`/`-`) plus the view in history (`replacePlace`): the marks are read back from the open view, as the desktop does, so Back and a reload restore them | Views | new field, no new rule |
| The filter sheet's rows, per tab | `mobile-filter.js`: `sectionRows`/`rowTree` over the same `state.sections[name].index` Browse already reads | Views | new, ~350 lines |
| Which photos, in what order, how many | `/api/library/ids` and `/cards` through `library-source.js`; the filter is `kind=search` | Views | existing, unchanged |
| The live count on the sheet's button | `library_view.ids` length of the spec (the same read the grid makes), or the grid's own `viewCountSaid` once the sheet closes | Views | existing; the sheet asks ids with `cap` and the count comes from the reply, nothing new |
| Counts narrowed by the other filters | advanced-filtering.md cut 2 (`navigator?section=&within=`), **not built on the desktop yet**; this plan does not build it. The phone sheet shows whole-library counts until that cut exists, and says "of all photos" under the tab | Views | deferred, owned by advanced-filtering.md |
| Browse row cover thumbnail | new `GET /api/library/covers?section=&rows=` in `tagpup/web/tagpup_routes.py`, answered by `tagpup/services/library_view.py` `covers(library, section, rows)`: `rows` is JSON, a list of at most 24 `{id, kind, value, recursive}` (the row's model id and its source in the `specOfMembers` shape); for each row it asks `store.all_ids(... cap=1)` of the row's own source, every row in one transaction, and replies `{section, covers: [{id, photo, thumb}]}` in the order asked, `thumb` the thumbnail URL `thumbnails.url` makes (`/api/photo-thumb?id=&v=`, a clip's poster), so the page draws it as given; `photo` and `thumb` null for a row with no photo now | Views, Integration (the route) | new, ~150 lines; no new SQL rule: it reuses `ids` |
| The thumbnail image itself, its cache, placeholder | `GET /api/photo-thumb` (`services/thumbnails`), immutable by `v` | Views | existing |
| Thumbnails loaded lazily, a placeholder first | `loading="lazy"`, `decoding="async"`, a fixed-size box and tonal background in CSS; one `IntersectionObserver` in `mobile-browse.js` asks covers for the rows near the screen, 24 at a time | Views | new, small |
| Photo facts: Date Taken, title, people, keywords | the record `/api/library/photo?id=` that `mobile-edit.js` already reads (`state.edit.record`) and the card `mobile-photo.js` holds; shown by `mobile-photo.js` in a pull-up sheet; people and tags split by `vocabulary.js` (`leafOf`, `rootOf`, `samePerson`) | Views | existing data, new layout |
| Date Taken wording | one `whenSaid(record)` beside `viewCountSaid` in `web/common/` or `format.js` equivalent; the Edit sheet uses the same | Views | new, one place |
| Write a tag, title or Date Taken | unchanged: `POST /api/photos/bulk-tags`, `POST /api/photo/save-metadata` via `web/common/photo-write.js` | Files | existing, untouched |
| Library from the URL, update banner, request routing | `web/common/api.js`, `library.js`, `roots-banner.js` | Integration | existing |
| Tag and person rules | `web/common/vocabulary.js`, `validate.js` | Views | existing |
| The page's order of loading, wiring last | `mobile.js` only wires and starts last (`tests/frontend/page-state.test.mjs` rules) | Views | existing |

No row has two places. The tab bar, the filter sheet and the cover route are new; every rule they apply (what a filter is,
who is a person, how a path compares, how a photo is written) is imported.

## Failure modes the build must answer

- **Rapid taps**: two taps on the same filter row cycle it twice (off to `+` to `&`); the model is the Map, not the DOM, so
  the rendered state is always the Map's. The spec is rebuilt from the Map and replaces the view; a reply for an older
  spec is dropped (`lib === state.photos`, as the grid does today). Tapping a tab twice does not push two history steps
  (`samePlace` guard, as Browse rows do). Closing the sheet during a read does not lose the filter.
- **A filter with zero results**: never an empty grid with no words. The grid shows "No photo has all of these." with the
  conflicting chips named, a one-tap "Remove <last chip>" and "Clear all". The count line says "0 photos". The sheet's
  button reads "No photos" and is disabled only for the Show action, not for editing the marks. (A tag that has been
  deleted since the address was saved shows as a struck chip via `chipKnown`, as the desktop does.)
- **Slow thumbnails over a phone link**: every cell and browse thumbnail has a reserved box and placeholder, so nothing
  jumps; covers are asked only for rows within about two screens, at most 24 per request, and a reply for rows scrolled away
  is kept (cheap) but never blocks; a failed cover leaves the placeholder with the row still tappable; thumbnails are the
  existing immutable `v=` URLs (a second visit costs nothing); no cover is requested while the sheet or a photo is open.
- **Switching library**: unchanged from phone-viewer.md: another library is a whole-page navigation, so no filter, cover or
  chip of the old library can land in the new one. The filter is per page, not remembered across libraries.
- **Rotation and sizes (320 to 430 px, landscape)**: the tab bar stays at the bottom (landscape: icons beside the labels to
  save height); the grid column count comes from the CSS (`vgrid.js` reads it) so rotation re-measures through its existing
  resize path; the photo view moves its sheet beside the picture in landscape or lets it collapse to the handle; browse
  thumbnails keep a fixed square; no horizontal page scroll except the chip row's own; insets honoured on all four sides.
- **A read that fails**: the sheet's tab says "Could not load people. Try again" as Browse does today; the grid keeps its
  existing Try again; the cover route failing never blocks Browse.
- **Two at once**: the PC changes a tag while the phone filter is open; the next read follows it; no write is involved.
- **Real data shape**: a person with a single photo, a keyword path with many levels, a folder with 1,000 subfolders (the
  filter model's own caps, `MAX_MEMBERS`, apply and the sheet says so on the tab's status line, as the desktop does); a
  title of 500 characters (clamped to three lines with "more"); a clip's cover is its poster (the thumbnail route does that).
- **An update while working**: the existing "Reload this page" banner; a filter lives in the address, so a reload keeps it.
- **Back**: the filter sheet is a sheet step (like the Edit sheet): Back closes it, a second Back leaves the grid.

## Steady-state writer, one-offs

Nothing written by this feature. The cover route is a read, derived at request time from the existing ids and the thumbnail
cache (no stored cover, no migration, no backfill: a photo added or changed afterwards is picked up by the next read).
One-offs: none. Migrations: none. Scripts: none.

## Cuts (each leaves the trunk whole, under about 1,200 lines, one concern; Views workers unless noted)

1. **Look and shell (~700 lines, mostly CSS and moves).** Tokens into `theme.css`; `mobile.css` restyled on them (cards,
   chips, buttons with one primary style, sheets); the tab bar (`mobile-tabs.js`) with the four destinations; the top bar
   trimmed; browse rows and photo facts restyled with the data they already have (no cover yet, no filter yet); skeletons for
   grid cells; motion with reduced-motion off. The desktop page is untouched (its `style.css` keeps working from the same
   tokens, verified by its existing tests). Scenario tests: opening the page on each tab, Back from each tab, rotation
   keeps the tab bar, a library switched from the Libraries tab.
2. **Browse thumbnails (~500).** `covers` route + service + contract row, `mobile-browse.js` rows with a thumbnail and count,
   the People section as a horizontal row of round-cornered covers at the top of Browse, the lazy ask. Integration reviews
   the route (the spec test for an undocumented route, `tests/test_every_route_answers_this_pc_or_says_why.py`'s OPEN
   list, reply shape in the docs). Scenario tests: slow covers (a held reply), a failed cover, a row scrolled away before its
   reply, a section with 1,000 rows, a library switched mid-scroll.
3. **Filter on the phone (~900).** `mobile-filter.js` (sheet, tabs, three-state rows), the sticky chip row, the zero-result
   state, `state.filter`, history, the live count. Uses the desktop model and the existing server; the only new code is
   the drawing. Scenario tests: rapid taps on one row, filter then Back then forward, a filter with zero results then Remove,
   a bookmarked address naming a deleted tag, two libraries, a section read failing, a 6-chip filter at 320 px.
4. **Photo screen (~600).** Pull-up sheet with Date Taken, title/caption, people, keywords, `whenSaid`; the Edit button opens
   the existing sheet. Scenario tests: a photo with no keywords, no date, a very long title, many keywords (wrapping), a clip,
   a swipe while the sheet is open, an edit saved and the facts redrawn, rotation.
5. **Optional**: narrowed counts on the sheet (follows advanced-filtering.md cut 2, built on the desktop first), a person's
   face crop as the cover, a light theme. Not planned; the owner's call.

Total new about 2,700 lines over four merges, of which about 600 is replaced CSS (net smaller than it looks). Each merge
removes a concept it replaces: cut 1 the "Libraries" text button and the four plain section buttons, cut 3 the phone's two
raw Order/Show selects' share of the controls (they move into the sheet's header), cut 4 the grey `photo-at` line and its
styles. Cuts 1 and 2 can be built in parallel (disjoint files, Views both); 3 needs 1; 4 needs 1.

## Tests to add (scenario tests, owned by Views, mapped in `tests/owners.py`)

- `scenario-filtering-on-the-phone.test.mjs`: filter by two people and a keyword, NOT a keyword, Back restores the marks,
  zero results offers Remove, rapid taps, a deleted tag in an address, a library switch.
- `scenario-browsing-with-pictures-on-the-phone.test.mjs` (extends the existing browse scenario): covers load lazily, a slow
  one, a failed one, scrolled away.
- `scenario-looking-at-a-photo-on-the-phone.test.mjs` extended: the facts sheet, edge data (no date, long title, 40 keywords).
- `tests/test_library_covers.py`: the route answers the first photo of a row's source in the row's order, a row with no
  photos, a clip (its poster id), an id the library has no row for, a union of folders; run against a home of its own
  through `tests/photo_rows.py` (never real rows).
- Existing guards that must still pass unchanged: `page-state.test.mjs`, `page-modules.test.mjs`, dom-contract and
  dark-select tests for the phone page, the spec/route tests, `tests/test_layers.py`.
- A real-browser look at 360, 390 and 430 px portrait and 844 px landscape by a worker in a sandbox (screenshots into the
  report), as docs/DEVELOPMENT.md asks for a speed or look claim. No speed claim is made by this plan.

## Owner reviews and checks

Views reviews every cut. Integration reviews cut 1 (the page's shell, harness entry) and cut 2 (the new route, its reply
contract and the spec tests). Files, Data, People, Media review nothing (they are not touched); if a person's face crop is
ever wanted as the cover, People reviews that cut. Each cut is checked against the seven conditions: no reach-ins (the phone
page imports the model and `elements`-free functions, it never reaches into the desktop `state.js`), no rule written
twice (the filter model, vocabulary and the write chain are imported; the tokens live in one file), no hidden coupling, no
concept that could collapse, no branch without an instance (every failure mode above is a user action on a phone), scenario
tests owned and mapped, no open `data` or `wrong` finding. Each finding goes into docs/findings.md first.

## Questions for the owner (answered 2026-11-23)

1. Do the three mock-ups look right (tab bar of four, chips row above the grid, pull-up facts sheet on the photo)? **Yes.**
2. Tab names and whether "Library" (browse) or "Photos" (the grid) is the first tab the page opens on. **Library.**
3. A person's picture: a photograph of them or a cropped face? **A photograph** (no face-crop route).
4. Filter-sheet counts are whole-library until the desktop's narrowed counts exist? **Acceptable.**
5. Light theme? **Stays out: dark only.**

Sources for the research: Material 3 chip and navigation-bar guidance and Google Photos' navigation changes (9to5google,
Android Authority, PhoneArena, 2021-2025); Immich release notes v1.118, v2.4, v2.6 (linuxiac.com, immich.app); Apple Photos
iOS 18 redesign (MacRumors, TechCrunch, Tom's Guide, 2024). Lightroom mobile and PhotoPrism were not found this session.
