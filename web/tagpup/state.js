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

    // The open folder's photos found damaged, by pathKey (GET /api/folder/damaged), and
    // how many times it was asked, so an older answer does not overwrite a newer
    // (damaged.js).
    damagedPhotos: {},
    damagedAsked: 0,
    // Check again under way: its button is disabled, and a second click sends nothing.
    damagedChecking: false,
};
