// TagPup's page: what it holds while it is open. One object, not a module's `let`s: a
// module cannot assign to a binding it imports, so every module reads and writes the
// page's state through `state`.

export const state = {
    // App State
    scannedFolder: '',
    folderPhotos: [],
    activePhotoPath: null,
    // The selection: paths, in the order they were picked, which the bulk edits read; and
    // the same photos by pathKey, for O(1) membership. selected.js is the one writer of both.
    selectedThumbnails: [],
    selectedKeys: new Set(),
    lastSelectedPath: null,
    // The library view that is open (library-source.js, library-view.js), or null while a folder is: the
    // source's kind and value, the order of its photos (`ids`), the cards held, what is being fetched.
    // While it is open a folder's own state below is idle: scannedFolder is null and folderPhotos holds
    // only the photo being edited in full. libraryTokens numbers the views opened; libraryReturn is the
    // folder to go back to.
    library: null,
    libraryTokens: 0,
    libraryReturn: '',
    // The navigator beside the grid (navigator.js, navigator-model.js; docs/ARCHITECTURE.md, phase 9c). `shown` is the
    // sidebar's pane ('library' | 'folder'), `choice` the pane each kind of view shows, as the person last chose it
    // (until the page is left); `tab` the section shown; `followed` the source of the view that is open ({ kind,
    // value, recursive }) or null. A section holds what GET /api/library/navigator answered (`index`, built by
    // navigator-model.js), what is open in it (`expanded`, row ids), its filter text, the row the arrow keys are on
    // (`currentId`), the rows last drawn (`rows`, for the keys), and `asked`, the number of the request whose answer
    // counts: an older answer is dropped. `stale`: counts changed since it was read. `reveal`: the followed source
    // is to be shown in it once it is here.
    nav: {
        shown: 'folder',
        choice: { library: 'library', folder: 'folder' },
        tab: 'folders',
        followed: null,
        timer: null,
        sections: {
            folders: { status: 'idle', message: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, rows: [], asked: 0, stale: false, reveal: false },
            keywords: { status: 'idle', message: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, rows: [], asked: 0, stale: false, reveal: false },
            people: { status: 'idle', message: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, rows: [], asked: 0, stale: false, reveal: false },
            dates: { status: 'idle', message: '', index: null, expanded: new Set(), filter: '', filterTimer: null, currentId: null, rows: [], asked: 0, stale: false, reveal: false },
        },
    },
    // Moving between a folder on disk and its view of the library (library-moves.js): the photo to land on in the
    // view being opened ({ path, folder, toKind } -- the top of the grid when the move was made), `addFor` the folder
    // the add-folder question is asked of when it was asked from a library view, the folders whose
    // "not in the library" banner was dismissed (until the page is left), the banner shown, and the number of the
    // membership question whose answer counts; `cache` what the disk held of each folder (by pathKey), for the page's life
    // (library-banner.js).
    moves: { anchor: null, dismissed: new Set(), banner: null, asked: 0, controller: null, addFor: null, cache: new Map(), leaving: false },
    // When the library was last in step with its folders, for the view's strip (sync-state.js).
    syncInfo: { status: 'idle', lastInStep: null, syncing: false, known: false, asked: 0, controller: null },
    // The grid's keys (grid-keys.js): the index of the card the arrow keys are on (or -1), where a Shift run began
    // (-1: none), and whether the focus waits on the grid for a card that has not arrived.
    gridKeys: { index: -1, anchor: -1, waiting: false },
    // What to say once the folder just asked for again has been put on screen (folder.js
    // showScannedFolder): a refusal because the photo's file changed reads the folder, then tells the owner.
    afterScan: null,
    // The grid (vgrid.js, built by wireThumbnailGrid), what it is showing -- the folder's photos
    // after the filter, and where each is by pathKey -- and which folder and filter that was,
    // to tell a new list (scroll to the top) from the same one drawn again (keep the place).
    grid: null,
    shownPhotos: [],
    shownIndex: new Map(),
    shownSource: null,
    folderSuggestions: {},
    progressTimer: null,
    knownTags: [],
    knownPeople: [],
    taxonomyNodes: [],

    // Declared here with the rest of the state rather than beside renderPhotoFaces,
    // which is where it is used. showFolderView() reads it to abandon an in-flight
    // face lookup, and restoring a cached folder on page load calls showFolderView
    // while the closure body is still running -- before a `let` further down has been
    // initialised. That threw "Cannot access 'facesRequestToken' before
    // initialization", inside scanFolder's promise chain, where it surfaced as
    // "Error scanning folder" and left the folder unopenable until the cache expired.
    facesRequestToken: 0,

    // The Image Details write in progress, and the "Save changes?" question being
    // asked, if any. Up here for the same reason: restoring a cached folder at startup
    // reaches selectPhoto and showFolderView, which read both.
    detailSaveInFlight: null,
    leavePrompt: null,
    // The last write queued for one photo, by pathKey: what leaving that photo waits
    // for, where detailSaveInFlight, the tail of the whole queue, has bulk writes too.
    photoWrites: {},
    // What the selected photos of a library view carry, as the server counted it (tally.js): `status` idle | counting | ready | error,
    // `key` the selection (the view's token and the selection's version) it was asked for, `asked` the number of the request whose
    // answer counts, `data` the answer.
    tally: { status: 'idle', key: '', asked: 0, timer: 0, controller: null, data: null, message: '' },
    // The bulk edit of the library's photos the page is showing (bulk-job.js, bulk-strip.js): `job` is the status the server last
    // gave of it, `request` what was started ({ op, selection, params, desc }, for Start again), `starting` while the start is
    // out, `conflict` the sentence of a refused second start, `cancelling` after Cancel was pressed, `failures` the answers to
    // "how is it going" that did not come (`trouble` says so, `gaveUp` after too many), `timer`/`controller`/`asked` the poll, and
    // `endedKey` the job end already acted on (the counts, the cards, the caches), once, `asking` while an edit's question or
    // placement dialog is open (a second click on Add does not ask again), `notice` ({ took, picked }) the strip keeps beside
    // a running job (it took more photos than were picked).
    bulk: {
        job: null, request: null, starting: false, conflict: '', cancelling: false,
        failures: 0, trouble: '', gaveUp: false, timer: 0, controller: null, asked: 0, endedKey: '', announced: null,
        attaching: null, attachedAt: 0, asking: false, notice: null,
    },
    // The photo write queue's entries, for its status (write-queue.js): each
    // { id, label, status: waiting | writing | done | failed, error, at, retry }.
    // batchTotal and batchSettled count the writes since the queue was last empty.
    // batchSkipped counts the photos those writes skipped as damaged (write-queue.js).
    writeQueue: { entries: [], nextId: 1, batchTotal: 0, batchSettled: 0, batchSkipped: 0, open: false },

    // The title showPhoto last put in the field, and for which photo. A refresh shows
    // the same photo again; if the field no longer holds what was put there, somebody
    // is typing, and the refresh must not write over it. Up here for the same reason.
    titleShown: { path: null, title: '' },

    // Abort controller for scan fetches
    scanAbortController: null,

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

    indexProgressTimer: null,

    // What the library holds of the open folder (GET /api/folder/membership), and the
    // folder being looked at without adding it, if any: edits go to the photo files only, and
    // Suggest and face naming are held back there until it is added (membership.js).
    folderMembership: null,
    justLooking: null,

    // The folder whose suggestions on this page were found by a Suggest that only looked (the status said
    // `in_memory`): kept in the server's memory, not the library's. Once the library holds the folder, or
    // the server has let them go, Suggest is run again (suggestions.js).
    suggestionsInMemory: null,

    // The open folder's photos found damaged, by pathKey (GET /api/folder/damaged), and
    // how many times it was asked, so an older answer does not overwrite a newer
    // (damaged.js).
    damagedPhotos: {},
    damagedAsked: 0,
    // Check again under way: its button is disabled, and a second click sends nothing.
    damagedChecking: false,
};
