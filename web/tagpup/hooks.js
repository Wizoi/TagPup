// TagPup's page: what a feature calls in a module above it.
//
// A module cannot import one that imports it: a browser would load the two, but the page
// tests load a page's modules as one script in import order and refuse a cycle
// (tests/frontend/harness.mjs). Redrawing the list, the grid and the open photo is
// called from every feature, and each of those calls back into the features, so a call
// back up the page goes through here. main.js fills it in before anything runs.
export const upper = {
    addedFromView: null,
    applySuggestedTagDirect: null,
    checkDamagedPhotos: null,
    checkFolderMembership: null,
    checkSuggestionsStatus: null,
    choosePane: null,
    deleteSelection: null,
    landOnAnchor: null,
    leaveLibraryView: null,
    libraryChanged: null,
    libraryViewPainted: null,
    navigatorCountsChanged: null,
    navigatorFollows: null,
    openInOrganize: null,
    photosDeleted: null,
    photosWritten: null,
    populateCameraModelsDropdown: null,
    recordUndo: null,
    reloadChangedPhoto: null,
    renderFileList: null,
    renderSyncInfo: null,
    renderSuggestionsPanel: null,
    renderTags: null,
    renderThumbnails: null,
    selectPhoto: null,
    updateCameraHighlights: null,
    updateCarryForwardState: null,
    updateFolderAutoApplyState: null,
    updateSelectedThumbnailsCount: null,
    updateSuggestButtonState: null,
};
