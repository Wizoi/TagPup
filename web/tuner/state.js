// The page's mutable state, in one object: a module that imports a binding cannot
// reassign it.
export const state = {
    allPhotos: [],
    activePhotoPath: null,
    allKnownPeople: [],
    // Everyone, including people hidden from autocomplete. allKnownPeople is what is
    // offered while typing; this answers whether a name already exists. Asking the
    // filtered list offered to create a hidden person as somebody new.
    everyKnownPerson: [],
    currentPhotoDetails: null,

    // Face Matching Mode State
    allPeopleWithCounts: [],
    // The same people in the order the sidebar shows them (people.js sets it when it draws them).
    shownPeople: [],
    activePersonName: null,
    lastLoadedPersonName: null,
    //: The person whose grid is being fetched right now. Redrawing the sidebar asks
    //: for the active person's grid unless it is already loaded; one still on its way
    //: was asked for again, cancelling the first request and starting the server's
    //: build over -- fifty seconds, on the largest grids.
    loadingPersonName: null,
    //: How many unclustered faces exist when the response only carries the first
    //: 500 of them. Null when the list is complete.
    activeFacesTotal: null,
    //: Which band the grid is currently showing -- 'likely', 'possible', 'ungrouped',
    //: 'matched', 'outlier'. Kept so the heading can be recounted after faces leave
    //: without re-deriving every face's band.
    activeFacesLabel: '',
    selectedFaceIds: [],
    //: Each rendered face's own best match, by face id. A selection of faces
    //: that each resemble a different person cannot be assigned to one name
    //: typed in a box, and the badge on each card already says who it is.
    suggestionByFaceId: new Map(),
    //: The faces as rendered, in order, so Shift can mean "everything between".
    renderedFaceOrder: [],
    //: Each rendered face's record, by id, and the cards taken out of this grid by an
    //: assign or an ignore -- with where they sat -- so Undo can put them back in
    //: place. Undo rebuilt the grid from a list that no longer held them: ten seconds
    //: on Unknown Faces, and the faces it had just restored were not in it.
    renderedFacesById: new Map(),
    removedFromGrid: new Map(),
    //: Where a Shift range measures from: the last face clicked on its own.
    selectionAnchorId: null,
    //: The faces a badge-filled name was meant for. A name typed by hand is the
    //: user's and is left alone; one put there by clicking a badge belongs to
    //: that face, and must not quietly follow a later, different selection.
    nameFilledForFaceIds: null,
    activePersonFaces: [],
    activeTab: 'matches', // 'matches' or 'outliers'
    modalSelectedFaceIds: [],

    // Abort controllers for ongoing fetch requests
    sidebarAbortController: null,
    detailsAbortController: null,
    sidebarDetailAbortController: null,

    // What the picker is currently offering, and which of it is ticked.
    pickerFolders: [],
    pickerSelected: new Set(),

    // Folders to review (review.js): those /api/sync/review lists, whether an Include or
    // Ignore is on its way, and the dialog's parts, built the first time it opens.
    review: { folders: [], busy: false, modal: null, body: null, status: null, opener: null },

    // Roots (roots.js): the dialog's parts, built the first time it opens; whether it is open and
    // which opening (`token`, so an answer that comes after it closed is let go); a move being made
    // (`busy`, so a second press of Move does nothing); the roots whose full check is being followed
    // (`watching`) and when to ask again (`timer`, `pollMs`, which the server sets).
    roots: { modal: null, body: null, status: null, opener: null, open: false, token: 0, busy: false,
             watching: new Set(), timer: null, pollMs: 1000 },

    // Remove Folder: the library's folders (/api/folder/indexed), and the one chosen.
    removeFolders: [],
    removeFolderChosen: null,

    // The last folder whose poll reached a terminal status. index-active and
    // index-status are two reads of state that moves between them, so the first can
    // still name a folder the second has already called finished. Following that
    // answer would start the poll again, which would finish again, with nothing
    // between the two -- a spin, not a wait. Remembering the folder breaks it.
    lastFinishedFolder: null,
    followQueueTimer: null,
    indexPollTimer: null,

    gridBuildTimer: null,

    pendingIgnore: null,

    // The folder Re-examine is asking about or naming faces in (reexamine.js), or null:
    // one at a time, so a second press cannot ask again while the first is answered.
    reexamining: null,

    assignUndoTimer: null,

    allTags: [],
    tagBuckets: {},
    activeTag: null,
    activeBucket: null,
};
