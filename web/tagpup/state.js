// TagPup's page: what it holds while it is open. One object, not a module's `let`s: a
// module cannot assign to a binding it imports, so every module reads and writes the
// page's state through `state`.

import { PeopleDirectory } from './common/vocabulary.js';

export const state = {
    // App State
    scannedFolder: '',
    folderPhotos: [],
    activePhotoPath: null,
    // A delete of the panel's photo is out: its Delete is inert until the reply (stale.js holdDeletes).
    deleting: false,
    // The selection: paths, in the order they were picked, which the bulk edits read; and
    // the same photos by pathKey, for O(1) membership. selected.js is the one writer of both.
    selectedThumbnails: [],
    selectedKeys: new Set(),
    lastSelectedPath: null,
    // The library view that is open (library-source.js, library-view.js), or null while a folder is: the
    // source's kind and value, the order of its photos (`ids`), the cards held, what is being fetched.
    // While it is open a folder's own state below is idle: scannedFolder is null and folderPhotos holds
    // only the photo being edited in full. libraryTokens numbers the views opened.
    library: null,
    libraryTokens: 0,
    // The navigator beside the grid (navigator.js, navigator-model.js; docs/ARCHITECTURE.md, phase 9c). `shown` is the
    // sidebar's pane ('library' | 'folder'), `choice` the pane each kind of view shows, as the person last chose it
    // (until the page is left); `tab` the section shown; `followed` the source of the view that is open ({ kind,
    // value, recursive }) or null. A section holds what GET /api/library/navigator answered (`index`, built by
    // navigator-model.js), what is open in it (`expanded`, row ids), its filter text, the row the arrow keys are on
    // (`currentId`), the rows last drawn (`rows`, for the keys), and `asked`, the number of the request whose answer
    // counts: an older answer is dropped. `stale`: counts changed since it was read. `reveal`: the followed source
    // is to be shown in it once it is here. Several rows are selected at once (#672): the selection is the followed source
    // itself (a union of the rows' sources, held in the address), so a section keeps only `anchor`, the row a Shift-click
    // runs from, `notice`, a sentence about the last click (too many rows), and `opened`, whether its first index was opened
    // (People's branches start open). `order` is the order views are read in, as last chosen or opened (#671); `media` what they
    // show, the same way (filter-menu.js: 'both', 'photo' or 'video'; a page loaded opens on 'both'). `pending`: the
    // section's read under way, for another feature waiting on it (the search's picker, navigator.js readSectionIndex).
    // `filter` is Filter mode (filter-bar.js): `on` as this browser remembers it, `leaving` while a search is being left
    // (library-view.js leaveSearch; navigatorFollows clears it, so a second click cannot go back twice), and `notice`, the
    // sentence refusing the last mark (too many rows). The marks themselves are the view open's, never kept here.
    nav: {
        shown: 'folder',
        choice: { library: 'library', folder: 'folder' },
        tab: 'folders',
        followed: null,
        order: 'taken',
        media: 'both',
        timer: null,
        filter: { on: false, leaving: false, notice: '' },
        sections: {
            folders: { status: 'idle', message: '', notice: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, anchor: null, rows: [], asked: 0, stale: false, reveal: false, opened: false, pending: null },
            keywords: { status: 'idle', message: '', notice: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, anchor: null, rows: [], asked: 0, stale: false, reveal: false, opened: false, pending: null },
            people: { status: 'idle', message: '', notice: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, anchor: null, rows: [], asked: 0, stale: false, reveal: false, opened: false, pending: null },
            dates: { status: 'idle', message: '', notice: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, anchor: null, rows: [], asked: 0, stale: false, reveal: false, opened: false, pending: null },
        },
    },
    // Moving between a folder on disk and its view of the library (library-moves.js): the photo to land on in the
    // view being opened ({ path, folder, toKind } -- the top of the grid when the move was made), `addFor` the folder
    // the add-folder question is asked of when it was asked from a library view, the folders whose
    // "not in the library" banner was dismissed (until the page is left), the banner shown, and the number of the
    // membership question whose answer counts; `cache` what the disk held of each folder (by pathKey), for the page's life
    // (library-banner.js).
    moves: { anchor: null, dismissed: new Set(), banner: null, asked: 0, controller: null, addFor: null, cache: new Map(), leaving: false },
    // Where the page is in this tab's history (library-view.js, #770): each place it made is tagged with this load's id and its
    // position, and `at` is the position of the place the page shows (null: not known -- a place another load made, or one a
    // folder's address replaced). A Back or Forward cancelled for unsaved edits goes back to `at`.
    entries: { load: Math.random().toString(36).slice(2), at: null },
    // The library view as it was left when a photo was opened over it (view-left.js, #780): { token, scrollTop, anchorId, anchorIndex },
    // or null while the grid is what is shown.
    viewLeft: null,
    // A Back out of the photo was asked of the browser and has not arrived (view-left.js backToView): a second asks nothing.
    backUnderway: false,
    // Whether the Selection Details panel is opened over the grid in a narrow window (details-panel.js, #720); a wide window
    // always shows it beside the grid.
    detailsPanel: { open: false },
    // The Sort by menu of a view's header (sort-menu.js, #714): whether it is open, and the token of the view it was drawn for.
    sortMenu: { open: false, token: 0 },
    // Show, beside it (filter-menu.js): the same.
    filterMenu: { open: false, token: 0 },
    // The search box of the Library pane (search.js, phase 9e-2). `lists` the chips of All of, Any of and None of -- each a source
    // { kind, value, recursive }, as the open search holds them, or as just changed by the person before the search opens --;
    // `within` whether "Within the sidebar's selection" is ticked; `shownToken` the view the box last showed and `showing`
    // whether that was a search; `note` the sentence by the box (`problem`: it says something went wrong). The picker: the list
    // it adds to, what is offered (`options`, { member, label, hint, count, what }), the one the keys are on (`active`), how
    // many more matched than are shown, the names it offers (`names`, made once for the indexes `from`), `asked`, the number
    // of the read whose answer counts, `reading` while the names are being read, and `enterWaits` when Enter was pressed
    // meanwhile (#771: the first name offered is added once they are here).
    search: {
        lists: { all_of: [], any_of: [], none_of: [] }, within: false, shownToken: -1, showing: false,
        note: '', problem: false, filtersOpen: false,
        picker: { list: null, options: [], active: -1, more: 0, asked: 0, names: [], from: null, message: '', reading: false, enterWaits: false },
    },
    // The selection details of a library view (selection-panel.js, #781): whether the Tagging section is open (this browser remembers it),
    // and which jump lists were opened past their first few links ("and N more").
    selectionPanel: { taggingOpen: false, expanded: { people: false, keywords: false } },
    // When the library was last in step with its folders, for the view's strip (sync-state.js).
    // `lastRunInStep`: whether the newest sync, of the whole library or a folder, left it in step (null: none, or not known).
    syncInfo: { status: 'idle', lastInStep: null, lastRunInStep: null, syncing: false, known: false, asked: 0, controller: null },
    // The grid's keys (grid-keys.js): the index of the card the arrow keys are on (or -1), where a Shift run began
    // (-1: none), and whether the focus waits on the grid for a card that has not arrived.
    gridKeys: { index: -1, anchor: -1, waiting: false },
    // What to say once the folder just asked for again has been put on screen (folder.js
    // showScannedFolder): a refusal because the photo's file changed reads the folder, then tells the owner.
    afterScan: null,
    // The folder a Refresh is syncing now (library-view.js syncFolderThenScan), or null: one at a time.
    folderRefresh: null,
    // The grid (vgrid.js, built by wireThumbnailGrid), what it is showing -- the folder's photos
    // after the filter, and where each is by pathKey -- and which folder and filter that was,
    // to tell a new list (scroll to the top) from the same one drawn again (keep the place).
    grid: null,
    shownPhotos: [],
    shownIndex: new Map(),
    shownSource: null,
    folderSuggestions: {},
    // The open folder's Suggest run as its last poll said it ({status, message, completed, total}), or null before
    // one answered: what the photo's suggestions box says while a run waits or is under way (suggestions.js).
    suggestRun: null,
    // Who the unnamed faces of each selected photo of the folder look like, {path: [{person, name, similarity}]}, [] for
    // none, and the ask still out for the rest ({key, controller}) (selection-faces.js).
    selectionFaces: {},
    selectionFacesAsking: null,
    progressTimer: null,
    knownTags: [],
    // The same people without those hidden from autocomplete: what the add-person field offers while typing.
    offeredPeople: new PeopleDirectory([]),
    // Everyone with a person tag, as /api/people?include_hidden=1 answers (a PeopleDirectory): who a tag or a typed name is, by id,
    // and each person's label where a name is shared. Filled by tags.js; empty until it answers.
    people: new PeopleDirectory([]),
    taxonomyNodes: [],

    // Declared here with the rest of the state rather than beside renderPhotoFaces,
    // which is where it is used. showFolderView() reads it to abandon an in-flight
    // face lookup, and restoring a cached folder on page load calls showFolderView
    // while the closure body is still running -- before a `let` further down has been
    // initialised. That threw "Cannot access 'facesRequestToken' before
    // initialization", inside scanFolder's promise chain, where it surfaced as
    // "Error scanning folder" and left the folder unopenable until the cache expired.
    facesRequestToken: 0,

    // The faces boxed on the open photo (face-boxes.js): `path` the photo, `faces` as /api/photo-faces gave them, `size`
    // the picture as shown that their `shown_box`es are in, `shown` the boxes are on
    // (kept from photo to photo), `open` the face whose panel is open, `matches` {face id: the people it looks like, or
    // 'loading' | {failed: the sentence why}}, `busy` a naming is under way, `note` the panel's last sentence, and `typed` the name being typed
    // in the panel of face `typedFor`.
    faceBoxes: {
        path: null, faces: [], size: null, shown: false, open: null, matches: {}, busy: false,
        // A save's reply listed a face while a naming was under way or while the photo's faces were still being read: they
        // are read again when the naming ends or the read answers (photo.js facesFollowReply).
        owed: false,
        // The namings under way: the faces' ids, and the one chain of their writes (face-boxes.js nameFaceAs).
        naming: [], waiting: [], writes: null,
        note: '', typed: '', typedFor: null,
    },

    // The Image Details write in progress, and the "Save changes?" question being
    // asked, if any. Up here for the same reason: restoring a cached folder at startup
    // reaches selectPhoto and showFolderView, which read both.
    detailSaveInFlight: null,
    // A clip playing when a write began: its id and where it was, so it plays on when the writes are done (media.js holdForWrite).
    heldForWrite: null,
    leavePrompt: null,
    // The one move off the open photo waiting for its writes or the question, the last asked:
    // { move, onStay, gone, note } (edits.js leavePhotoThen).
    heldMove: null,
    // The last write queued for one photo, by pathKey: what leaving that photo waits
    // for, where detailSaveInFlight, the tail of the whole queue, has bulk writes too.
    photoWrites: {},
    // What the selected photos of a library view carry, as the server counted it (tally.js): `status` idle | counting | ready | error,
    // `key` the selection (the view's token and the selection's version) it was asked for, `asked` the number of the request whose
    // answer counts, `data` the answer.
    tally: { status: 'idle', key: '', asked: 0, timer: 0, controller: null, data: null, message: '' },
    // The bulk edit of the library's photos (bulk-run.js): `running` while its request is out, `asking` while an edit's question or
    // placement dialog is open (a second click on Add does not ask again). The strip that shows it is web/common/batch-strip.js.
    bulk: { running: false, asking: false },
    // The photo write queue's entries, for its status (write-queue.js): each
    // { id, label, status: waiting | writing | done | failed, error, at, retry }.
    // batchTotal and batchSettled count the writes since the queue was last empty.
    writeQueue: { entries: [], nextId: 1, batchTotal: 0, batchSettled: 0, open: false },
    // The tag changes not yet sent (pending.js): `files` by pathKey, each {path, add, remove, panel, token, actions, folder}, in
    // the order they came; `queued` while a chunk's write waits its turn in the queue, `stopped` after one failed (until Retry or
    // Cancel); `people` the tags a change named as people, whose leaves the records' `people` follow; `drops` how many
    // times the waiting changes were dropped (Cancel, leaving), so a chunk sent before neither gives back nor says Ready;
    // `out` the chunk whose write is out; `reads` the replies taken while each library read is out (readHeld); `failed` the
    // row of the failure the status line says, which the drain's next words do not say over until it is dismissed.
    pending: { files: new Map(), queued: false, stopped: false, people: new Set(), drops: 0, out: null, reads: new Set(), failed: null },

    // The title showPhoto last put in the field, and for which photo. A refresh shows
    // the same photo again; if the field no longer holds what was put there, somebody
    // is typing, and the refresh must not write over it. Up here for the same reason.
    titleShown: { path: null, title: '' },
    // The grouping box beside the open photo's name (rename-one.js): the photo it was filled for, what was put in it,
    // and the number of the request whose answer counts (a reply for a photo left since is dropped).
    smartRenameOne: { path: null, filled: '', asked: 0 },
    // The clip panel's Convert and Make smaller (clip-convert.js): `offer`, what the buttons offer for the clip shown
    // ({route, clip, mode, replaces}, replaces null until its plan is asked), and `live`, the clips (by id) whose
    // conversion the server has taken or is being asked: {photo, from, ticket, library, note, cancelling}.
    clipConvert: { offer: null, live: {} },

    // Abort controller for scan fetches
    scanAbortController: null,
    // The scan out now (folder.js scanFolder), {path, stale}, else null: an index holding its photos that ends meanwhile marks
    // it `stale` (library-view.js indexEnded), and it reads the folder again as it lands rather than show what it read before.
    scanning: null,

    statusResetTimer: null,
    /**
     * The last undoable write, and how to put it back.
     *
     * One step deep on purpose. The mistake this catches is the one that actually
     * happens -- a bulk write against the wrong selection, noticed immediately -- and
     * a deeper stack would need the photo files to be the source of truth rather than
     * this snapshot, which they are, since anything can edit them behind our back.
     * Holding one step keeps that window short enough to be honest about.
     */
    lastUndoable: null,

    searchTimeout: null,

    // The library's indexes as this page follows them (suggestions.js checkIndexingStatus): `library`, the library they are
    // of; `known`, pathKey -> folder of each queued or being indexed, as last heard (GET /api/folder/index-active) or just
    // queued here; `counted`, the keys of every folder seen since the queue was last empty, and `ended`, how many of them
    // have ended ("Indexing N of M folders"); `timer`, the next ask; `asking`, one is out, and `again`, another
    // was wanted meanwhile; `failures`, asks in a row with no answer.
    indexFollow: {
        library: '', known: new Map(), counted: new Set(), ended: 0,
        timer: null, asking: false, again: false, failures: 0,
    },
    // How far the open folder's index has got while one runs (a percent, 0 while it waits), else null: a Suggest run waiting
    // for it shows it in its own bar.
    indexPercent: null,
    // Whether the Suggest run showing is waiting (for the index or the graphics card) and has no count of its own yet.
    suggestWaiting: false,

    // What the library holds of the open folder (GET /api/folder/membership), and the
    // folder being looked at without adding it, if any: edits go to the photo files only, and
    // Suggest and face naming are held back there until it is added (membership.js).
    folderMembership: null,
    justLooking: null,

    // The folder whose suggestions on this page were found by a Suggest that only looked (the status said
    // `in_memory`): kept in the server's memory, not the library's. Once the library holds the folder, or
    // the server has let them go, Suggest is run again (suggestions.js).
    suggestionsInMemory: null,
};
