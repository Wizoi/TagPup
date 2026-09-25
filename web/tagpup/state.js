// TagPup's page: what it holds while it is open. One object, not a module's `let`s: a
// module cannot assign to a binding it imports, so every module reads and writes the
// page's state through `state`.

export const state = {
    // App State
    scannedFolder: '',
    folderPhotos: [],
    activePhotoPath: null,
    selectedThumbnails: [],
    lastSelectedPath: null,
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
};
