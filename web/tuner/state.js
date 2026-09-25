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

    assignUndoTimer: null,

    allTags: [],
    tagBuckets: {},
    activeTag: null,
    activeBucket: null,
};
