# Phone viewer: the owner opens a simple page on a phone, finds photos, looks at one large, and fixes its tags, title and Date Taken

**Status: a plan, nothing built.** Written 2026-11-17 at the owner's request. Builds on
[family-read-only-access-over-tailscale.md](family-read-only-access-over-tailscale.md) (how the phone reaches the PC) and
adds what that plan left out (the page).
Live count: not a data feature (it writes only through the routes the desktop page already uses). The surface it reads:
the library routes below, all existing. Phone use today: the desktop page at 390 px is unusable (counted from the CSS
2026-11-17: the sidebar is a fixed 360 px, three `max-width` queries in 3,057 lines, none for the sidebar).
Domains: Views (the page, its browse and photo screens); Integration (serving a second page, the route tests, the harness);
Files only for the one shared writer (cut 3). Integration reviews the seams.

## What the owner gets, and what stays on the PC

Gets: pick a library; browse by Folder, Keyword, Person or Date, or search words; a grid of thumbnails; open a photo large
(swipe to the next, pinch to zoom, a clip plays); see its people, keywords, title/caption and Date Taken; add or take off a
person or keyword; edit title/caption and Date Taken; each save says "Saved" or says why not.

Stays on the PC (not on the phone page at all, so nothing to hide): the gear and everything in it, TagTuner, Activity,
Settings, History and Undo, Name faces and face boxes, Suggest and Auto-Apply, bulk edit and multi-select, rename and time
shift, rotate, delete, Convert, add folder / sync / indexing, creating a library. "Oldie mobile" is read as: one column, big
type, big tap targets, few buttons, no hover, no drag, no right-click.

## Decision: a second page, not the desktop page made responsive

Counted from the code (research 2026-11-17): the desktop page is 637 lines of HTML, 3,057 of CSS, 55 modules (about 15.5 k
lines). Every feature module reads ids through `elements.js` (null if missing, many do not check) and calls back up through
`upper` (about 44 hooks, null until `main.js` fills them). `photo.js`, `edits.js` and `tags.js` cannot be mounted in another
layout without stubbing both. Making that page fit a phone means a second set of layout rules over every region, and the
phone would still carry the gear and bulk editing. A second page of its own, built from the modules that have no such
coupling, is smaller and cannot break the desktop page. The cost is a second set of renderers; the rule below keeps it from
becoming a second copy of any rule.

## Mechanisms: the ONE place each lives

| Mechanism | The ONE place it lives | Owner | New or existing |
|---|---|---|---|
| The phone reaches the PC | `tailscale serve --bg 8090` (TagPup is 8090, TagTuner 8080), host name in `TAGPUP_ALLOWED_HOSTS` (`security.allowed_hosts`) | Integration | existing, a setup step (cut 0) |
| Serve a second page: `/<library>/mobile.html`, its `mobile.css`, its modules | `tagpup/web/app.py` `_page_routes` and `libraries.PAGE_FILES`; modules are flat `web/tagpup/mobile*.js` (the flat `/<name>.js` rule already serves them) | Integration | existing mechanism, one added page |
| Colours, fonts, spacing (dark theme) | `web/common/theme.css`: the `:root` tokens moved out of `style.css`, which then imports them; `mobile.css` imports the same file | Views | new file, moved not copied |
| Library from the URL, request routing, the update banner | `web/common/api.js`, `library.js`, `roots-banner.js` | Integration | existing, imported as they are |
| What a view lists and in what order (folder, keyword, person, year, search) | `/api/library/ids` and `/api/library/cards` through `web/tagpup/library-source.js` (`lib`, `loadLibraryIds`, `askForCards`) and the tree models `navigator-model.js`, `search-model.js` | Views | existing, imported |
| Windowed grid of cards | `web/tagpup/vgrid.js` `createVGrid` (reads its column count from the CSS, so 2 to 3 columns at 390 px) | Views | existing, imported |
| The page's own state and its screens (browse, grid, photo, details sheet) | `web/tagpup/mobile-*.js`: `mobile-state.js`, `mobile-browse.js`, `mobile-grid.js`, `mobile-photo.js`, `mobile-details.js`; `mobile.js` only wires and starts last | Views | new |
| Swipe to the next photo | `enableSwipeNavigation` moved from `navigation.js` to `web/common/swipe.js`; both pages import it | Views | moved, not copied |
| Pinch zoom, a large photo | `web/common/image-zoom.js` | Views | existing |
| Tag and person rules (what may be set, leaf vs path) | `web/common/vocabulary.js`, `validate.js` and `GET /api/rules`, `/api/tags`, `/api/people`; the phone's suggestion list is a plain list under the input, not a `<datalist>` | Views | existing rules, new small control |
| Write one photo's tags | `POST /api/photos/bulk-tags {changes: {path: {add, remove}}}` for the one path: adds and removes against what the file holds, so no stamp and no whole-list race; checks of what is added happen on the server | Files | existing, one path |
| Write title/caption and Date Taken | `POST /api/photo/save-metadata` with the photo's `stamp` and `base`, through `postPhotoMetadata` moved from `edits.js` to `web/common/photo-write.js` and imported by both pages | Files | moved, not copied |
| Order of a phone's writes (a second save waits for the first) | `web/common/photo-write.js`: the one-at-a-time chain `queuePhotoWrite` already is, with `edits.js` calling it | Views | moved with the writer |
| A save the file refused (changed on disk, 409) | the reply's sentence shown as it comes; the photo is re-read (`/api/library/photo?id=`) and the sheet redrawn; no retry loop | Views | existing replies, new wording on the phone |
| Which photo path is which | `pathKey`, `samePath` (`web/common/paths.js`) | Views | existing |

The phone writes through the same two routes and the same server writer as the desktop page: the server's one writer is
`file_changes.write_fields` through the journaled change, so a phone edit appears in History and can be undone from the PC.
No new route, no migration, no schema change.

## Screens

1. **Library**: the libraries from `GET /api/databases`, one tap; remembered (`rememberedLibrary`, as the desktop page does).
2. **Browse**: a search box and four big rows (Folders, Keywords, People, Dates) from
   `GET /api/library/navigator?section=`. A row opens its children one level at a time (a drill-down list with a Back bar
   and the path as breadcrumb), not a tree; "Show these photos" at every level (`recursive` for a folder).
3. **Grid**: `lib` ids, cards in `createVGrid`, 3 columns at 390 px (2 for large type), sort and Photos/Clips only. A tap opens
   the photo (no selection mode). Count and "Showing N of M" line.
4. **Photo**: the picture at `photo-file?size=1024` (about 150-300 KB; the desktop uses 800), swipe left or right through
   the grid's ids, a tap to zoom, a clip through `/api/video-file`. Under it the facts: Date Taken, title/caption, people,
   keywords, a sheet that slides up for editing.
5. **Edit sheet**: people and keywords as chips, the same x as the desktop on each chip but asks (or shows an Undo toast for
   five seconds) rather than removing on a stray touch; an add box with a list of matching tags as you type; Title/Caption
   text; Date Taken as the native date and time inputs; one Save. A dirty sheet says so before it closes.

Deep links keep working (`mobile.html?view=keyword&value=...`, the same `VIEW_PARAMS`), so a link to a person's photos can
be sent to a phone.

## Failure modes the build must answer

- Interrupted: the phone drops off the cellular network mid-save: the save is a normal request; the sheet keeps the edit
  with "Not saved: no connection. Try again", and the photo is re-read before the next save (a half-sent request may or may
  not have written).
- Two at once: the phone and the PC edit the same photo: the stamp mismatch is a 409; the tag adds/removes (bulk-tags)
  are not stamped and cannot lose the PC's tags. A title saved from the phone over a PC's newer title is refused by the stamp.
- A read that fails: the grid says "Could not load photos. Pull to retry", never an empty grid; a card whose thumbnail
  fails shows the placeholder the server sends.
- The real data's shape (to be counted read-only before cut 3, not assumed): a photo can hold many keywords and several
  people; the keyword list wraps and scrolls inside the sheet; names are leaves, tags paths (`leafOf`, `rootOf`); a clip the browser cannot play
  shows its poster and "Play this on the PC" (Convert is the PC's).
- Rapid taps: a second tap on a card or on Save while the first is in flight does nothing visible twice (the write chain).
- Switching library mid-folder: another library is a whole-page navigation to `/<name>/mobile.html` (`goToLibrary`), as
  on the desktop page, so nothing of the old library is carried over or reset and no reply for it can land; an in-page
  reset has no live instance and is not built.
- An update while working: the existing "Reload this page" banner (`roots-banner.js`) appears; unsent edits stay in the
  sheet until the reload; the page is not reloaded for the owner.
- Large text and small screens (320 to 430 px wide, portrait and landscape): no horizontal scroll; 44 px minimum tap
  targets; inputs at 16 px so iOS does not zoom; safe-area insets honoured.
- Offline or slow: image sizes are small and cached (the `v=mtime` URLs are immutable); no offline mode is built.

## What it cannot do yet, and one thing to settle before relying on it

- **The server does not tell a phone from the owner.** Through Serve the request arrives from loopback, so everything the
  PC can do the phone can do, by hand-made request too (DECISIONS 2026-11-14). The phone page simply does not offer the
  rest. If a family member is to use it read-only, the role plan above (cuts 1-2 of that plan) must come first; this plan
  does not build it.
- **The port in the Tailscale plan is wrong.** Its step 3 says `tailscale serve --bg 8080`, which is TagTuner; TagPup is
  8090 (`ARCHITECTURE.md` lines 227-228, `tagpup_web.py`). The serve the owner left running on 2026-11-14 points at 8080
  and has to be changed to 8090 (cut 0), and the plan's line corrected.
- **`/api/apps` builds the other app's address from the request** and is wrong through Serve (HTTPS 443, not a port): the
  phone page never calls it.
- HEIC and other formats the library does not index are not in the library, so not on the phone.
- The thumbnail route answers by id and caches; the resized photo route re-encodes each time (no server cache). One photo
  open is one encode and the browser keeps it a day; a server cache is added only if the PC is seen to labour.

## Steady-state writer, one-offs

No data is written by the page itself; every write goes through `bulk-tags` or `save-metadata` and the server's journaled
writer. A photo added or changed afterwards needs no command. One-offs: none. Migrations: none.

## Cuts (each leaves the trunk whole, under about 1,200 lines; a worker's brief is one concern)

0. **The owner, no code. Done 2026-11-17**: Tailscale is up on the phone; `tailscale serve` already has
   `https://staleorange.tail66259a.ts.net:8090` -> 8090 (TagPup; the bare address goes to 8080, TagTuner) and
   `TAGPUP_ALLOWED_HOSTS` is set, so the phone opens TagPup at the `:8090` address. What remains is the owner's look at
   today's page in Chrome on the phone, written down before cut 2.
1. **Integration (~250). Built 2026-11-17** (scenario I14, `tests/frontend/scenario-opening-the-phone-page.test.mjs`): serve `mobile.html` and `mobile.css`; `theme.css` split out of `style.css` (moved, not
   copied); the `PAGES` entry and an entry-module name in `tests/frontend/harness.mjs` (it requires `main.js` today);
   the `OPEN` list in `tests/test_every_route_answers_this_pc_or_says_why.py`; `tests/test_web_app.py` for the new page;
   dom-contract and dark-select tests cover the new page; the shell: library picker, the update banner, the empty screen.
2. **Views (~600)**: browse, search, grid (`mobile-state`, `-browse`, `-grid`, imports of `library-source`, `navigator-model`,
   `search-model`, `createVGrid`). Scenario tests: rapid taps, a library switched mid-folder, a failed list read, a search
   with no results, a link opened cold.
3. **Views + Files (~600)**: the photo screen and the edit sheet; `enableSwipeNavigation` moves to `common/swipe.js`;
   `postPhotoMetadata` and the write chain move to `common/photo-write.js` with the desktop page importing them (the
   desktop tests still pass unchanged, which proves the move). Scenario tests: add and remove a person and a keyword, a
   title save, a Date Taken save, a 409 from a changed file, a save lost to a dropped connection, a clip that will not
   play, an update during an edit.
4. **Optional, the owner's call**: roles (a viewer who cannot write) per the Tailscale plan, only if family is given the
   address; a server cache for resized photos; offline reading.

Total new about 1,450 lines over three merges plus the moved lines (not new). Each merge removes a concept it replaces or
says why (the two moves remove two copies the second page would otherwise have needed).

## Owner reviews and checks

Views reviews every cut; Integration reviews cut 1 and the contract rows of cut 3 (the route replies the page reads are
unchanged, no new route); Files reviews that cut 3 sends nothing the desktop page does not already send. Check each cut
against the seven conditions: no reach-ins (the phone page imports modules, it does not reach into `state.js` of the
desktop), no rule written twice (tag rules, the write chain and swipe are imported, not copied), no hidden coupling, no
concept that could collapse, no branch without an instance, scenario tests owned and mapped (`tests/owners.py`), no open
`data` or `wrong` finding.

## Answered by the owner (2026-11-17)

1. **The owner's own phone only** for now. The server's lack of roles is accepted for this; the role cuts stay optional
   (cut 4) and are needed only if family is ever given the address.
2. **Tags are worked with on the phone: add and remove**, people and keywords alike. Remove asks no confirmation but shows
   an Undo toast for five seconds (the removal is held, not sent, until the toast ends or the page leaves), so a stray
   touch is never a write.
3. **Android**, Chrome. Consequences built into the cuts: the hardware/gesture Back goes up one screen (each screen is a
   history entry: `pushState` for Grid, Photo and the sheet, so Back from a photo returns to the grid, not out of the
   page); the add-to-home-screen icon and manifest (Chrome installs a web page as an app) is cheap and moves from cut 4 to
   cut 1; the soft keyboard must not cover the add box (the sheet scrolls the input into view); date and time use the native
   Chrome pickers; pull-to-refresh on the grid is the browser's, so the page's own "retry" is a button; text size follows
   Android's font scale (rem units, no fixed pixel type). The look at today's page in cut 0 is made in Chrome on that phone.
