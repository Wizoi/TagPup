// The TagPup page: its start-up, and the wiring of each feature's module to the page.
// Every request goes through api.js, which puts the library in front of it
// (web/common/api.js).
import { initDatabaseSelector } from './common/library.js';
import { wireDialogKeys } from './common/dialog.js';
import { pressable } from './common/press.js';
import { wireBatchStrip } from './common/batch-strip.js';
import { wireRootsBanner } from './common/roots-banner.js';
import { loadRules } from './common/validate.js';
import { upper } from './hooks.js';
import {
    btnAddPerson, btnAddTag, btnApplyAllSingleSugg, btnApplyTimeshift, btnBrowseFolder,
    btnBulkAddPeople, btnBulkAddTags, btnCarryForward, btnCreateDb, btnDeletePhoto, btnDeleteSelection,
    btnFolderAutoApply, btnMirror, btnRefreshList, btnRotateLeft, btnRotateRight, btnSaveTitle,
    btnSelectAllThumbnails, btnSelectNoneThumbnails, btnSuggestCancel, btnSuggestTags, btnSuggestTitleWand,
    btnUndo, bulkAddPeopleInput, bulkAddTagsInput, dbSelect, detailFileName, folderPathInput,
    folderViewHeader, inputAddPerson, inputAddTag, inputPhotoTitle, mainImage, photoSearch, thumbnailsGrid
} from './elements.js';
import { fetchKnownTagsAndPeople } from './tags.js';
import { wireUnsavedEdits } from './edits.js';
import { leaveThen, removePhotoTags, wirePending } from './pending.js';
import {
    browseFolder, filterFileList, renderFileList, scanFolder,
    wireChangeDogPark, wireFolderPathInput, wireSidebarResizer
} from './folder.js';
import { wireTagPupGear } from './gear.js';
import { wireNameFaces } from './name-faces.js';
import {
    indexEnded, leaveLibraryView, libraryChanged, openLibraryView, openViewFromAddress, photosDeleted, refreshFolderOrView,
    wireLibraryView
} from './library-view.js';
import { wireSelectionPanel } from './selection-panel.js';
import { wireDetailsPanel } from './details-panel.js';
import { wirePhotoPath } from './photo-path.js';
import { restoreViewAsLeft, showGrid, wireViewLeft } from './view-left.js';
import { deleteSelection } from './bulk-edit.js';
import { checkFolderMembership, wireMembership } from './membership.js';
import { choosePane, navigatorCountsChanged, navigatorFollows, wireNavigator } from './navigator.js';
import { filterFollows, wireFilterBar } from './filter-bar.js';
import { showSortOrder, wireSortMenu } from './sort-menu.js';
import { showMediaFilter, wireFilterMenu } from './filter-menu.js';
import { searchFollows, searchVocabularyChanged, wireSearch } from './search.js';
import { addedFromView, wireBanner } from './library-banner.js';
import { renderSyncInfo } from './sync-state.js';
import { selectionWritten } from './tally.js';
import { landOnAnchor, libraryViewPainted, openInOrganize, wireMoves } from './library-moves.js';
import {
    carryTagsForward, deleteActivePhoto, facesFollowReply, openLibraryPhoto, openPhotoInDefaultApp,
    renderPhotoFaces, renderPhotoFacesHere, renderTags, rotatePhoto,
    reloadChangedPhoto, saveSingleAddPerson, saveSingleAddTag, saveSingleTitle, selectPhoto,
    updateCarryForwardState, wireZoom
} from './photo.js';
import { wireDateTakenModal } from './date-taken.js';
import {
    renderThumbnails, selectAllThumbnails, selectNoneThumbnails, wireGridContextMenu,
    wireThumbnailGrid, wireThumbnailSize
} from './grid.js';
import { recordUndo, undoLastOperation } from './undo.js';
import { stepPhoto, wireKeyboard } from './navigation.js';
import { enableSwipeNavigation } from './common/swipe.js';
import { wireFaceBoxes } from './face-boxes.js';
import { holdForWrite, resumeAfterWrite, wireMedia } from './media.js';
import { wireEditToggles } from './edit-toggles.js';
import {
    bulkAddPeopleToSelection, bulkAddTagsToSelection, updateSelectedThumbnailsCount
} from './selection.js';
import {
    applyAllSingleSuggestions, applyFolderSuggestionsLevel, applySuggestedTagsDirect,
    applySuggestedTitle, checkIndexingStatus, checkSuggestionsStatus, forgetFolderSuggestions, renderSuggestionsPanel,
    cancelSuggestions, startSuggestions, suggested, updateFolderAutoApplyState, updateSuggestButtonState
} from './suggestions.js';
import {
    applyTimeShift, populateCameraModelsDropdown, updateCameraHighlights,
    wireRenameAndTimeShift
} from './rename.js';
import { wireSmartRenameOne } from './rename-one.js';
import { wireClipConvert } from './clip-convert.js';

// What a feature calls in a module above it (hooks.js).
Object.assign(upper, {
    addedFromView, applySuggestedTagsDirect, checkFolderMembership, checkIndexingStatus, checkSuggestionsStatus, choosePane,
    facesFollowReply, filterFollows, forgetFolderSuggestions, holdForWrite, indexEnded, resumeAfterWrite,
    populateCameraModelsDropdown, recordUndo, removePhotoTags,
    renderFileList, renderPhotoFaces, renderPhotoFacesHere, renderSuggestionsPanel, renderSyncInfo, renderTags, renderThumbnails, searchFollows, searchVocabularyChanged,
    selectPhoto,
    landOnAnchor, leaveLibraryView, libraryChanged, libraryViewPainted, navigatorCountsChanged, navigatorFollows, openInOrganize,
    openLibraryPhoto, openLibraryView, restoreViewAsLeft,
    photosDeleted, selectionWritten, reloadChangedPhoto, showMediaFilter, showSortOrder, suggested,
    updateCameraHighlights, updateCarryForwardState, updateFolderAutoApplyState,
    updateSelectedThumbnailsCount, updateSuggestButtonState
});

document.addEventListener('DOMContentLoaded', () => {
    // What may be set, as the server says (web/common/validate.js): once.
    loadRules();
    // The picker and the library this browser remembers (web/common/library.js).
    // Another dog park is another page. Ask about unsaved edits here, with Save on
    // offer, rather than leave it to the browser's bare "Leave site?"; staying puts
    // the list back on the dog park still open.
    initDatabaseSelector(dbSelect, btnCreateDb, {
        beforeLeaving: (go, stay) => leaveThen(go, stay),
    });
    wirePending();

    wireChangeDogPark();

    // The library's name in the header, and asking before a folder it does not hold is
    // added to it (membership.js).
    wireMembership();

    // Load Datalists on Startup
    fetchKnownTagsAndPeople();

    // Event Listeners setup
    btnBrowseFolder.addEventListener('click', browseFolder);
    wireFolderPathInput();
    btnSuggestTags.addEventListener('click', startSuggestions);
    btnSuggestCancel.addEventListener('click', cancelSuggestions);
    btnRefreshList.addEventListener('click', refreshFolderOrView);
    
    photoSearch.addEventListener('input', filterFileList);
    pressable(folderViewHeader).addEventListener('click', showGrid);
    
    btnSelectAllThumbnails.addEventListener('click', selectAllThumbnails);
    btnSelectNoneThumbnails.addEventListener('click', selectNoneThumbnails);
    btnDeleteSelection.addEventListener('click', () => deleteSelection());
    btnBulkAddPeople.addEventListener('click', bulkAddPeopleToSelection);
    btnBulkAddTags.addEventListener('click', bulkAddTagsToSelection);
    bulkAddPeopleInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddPeopleToSelection(); });
    bulkAddTagsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddTagsToSelection(); });
    btnFolderAutoApply.addEventListener('click', applyFolderSuggestionsLevel);
    
    btnRotateLeft.addEventListener('click', () => rotatePhoto('left'));
    btnRotateRight.addEventListener('click', () => rotatePhoto('right'));
    btnMirror.addEventListener('click', () => rotatePhoto('mirror'));
    btnDeletePhoto.addEventListener('click', deleteActivePhoto);
    
    btnSaveTitle.addEventListener('click', saveSingleTitle);
    btnAddPerson.addEventListener('click', saveSingleAddPerson);
    btnAddTag.addEventListener('click', saveSingleAddTag);
    inputPhotoTitle.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleTitle(); });
    inputAddPerson.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddPerson(); });
    inputAddTag.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveSingleAddTag(); });

    wireUnsavedEdits();

    btnSuggestTitleWand.addEventListener('click', applySuggestedTitle);
    btnApplyAllSingleSugg.addEventListener('click', applyAllSingleSuggestions);
    detailFileName.addEventListener('click', openPhotoInDefaultApp);
    wirePhotoPath();
    btnApplyTimeshift.addEventListener('click', applyTimeShift);
    
    wireThumbnailGrid();
    wireThumbnailSize();
    wireRenameAndTimeShift();
    wireSmartRenameOne();

    wireKeyboard();

    wireSidebarResizer();

    wireGridContextMenu();

    // Views of the library: the strip above the grid, and Back and Forward between views and folders.
    wireLibraryView();

    // Filter mode as this browser remembers it, and its chips above the grid: before the navigator, which draws its toggle.
    wireFilterBar();
    // The navigator beside the grid, the move between a folder and its view, and the banner for photos the library lacks.
    wireNavigator();
    wireSearch();
    wireSortMenu();
    wireFilterMenu();
    wireMoves();
    wireBanner();

    // The selection details: the Tagging section's button, and what this browser remembers of it.
    wireSelectionPanel();

    // In a narrow window the selection details are behind a button, over the grid when opened.
    wireDetailsPanel();

    // The photo panel's Back, to the grid of a library view as it was left.
    wireViewLeft();

    enableSwipeNavigation(mainImage, stepPhoto);

    if (btnCarryForward) btnCarryForward.addEventListener('click', carryTagsForward);
    if (btnUndo) btnUndo.addEventListener('click', undoLastOperation);

    wireZoom();
    wireFaceBoxes();
    // A video in the photo panel: stopped when the panel is hidden, its failure said (media.js).
    wireMedia();
    // Its Convert and Make smaller (MP4), beside its Delete (clip-convert.js).
    wireClipConvert();
    wireEditToggles();

    wireDateTakenModal();

    // Every dialog's Escape, Tab and focus given back (web/common/dialog.js).
    wireDialogKeys();

    // The gear: the tag editor (web/common/tag-editor.js) and TagTuner on this library.
    wireTagPupGear();

    // The folder view's "Name faces from tags" (the gear's item is wired with the gear).
    wireNameFaces();

    // A library whose root this computer does not place says so, at the top of the page.
    wireRootsBanner();

    // The strip of the batches of photo files; it asks once, so a reload shows one still going. Dismiss leaves the focus on the grid.
    wireBatchStrip({ restoreFocus: () => thumbnailsGrid && thumbnailsGrid.focus({ preventScroll: true }) });

    // ---- Start ------------------------------------------------------------
    //
    // Last, deliberately. This opens the folder in the ?path= and picks up the indexes
    // already under way, which means it calls into most of the app: through the
    // listeners wired above and the calls up the page that main.js fills in (hooks.js).
    // Started before those lines, it would run without them.
    //
    // main.js declares no state of its own (tests/frontend/page-state.test.mjs), so
    // nothing here reaches a binding before its line has run; what is left of the rule
    // is this block's position, and tests/frontend/tag-vocabulary.test.mjs keeps it.
    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get('path');
    // What the library indexes now, whoever started it, followed to its end (suggestions.js).
    checkIndexingStatus();
    // A `?view` in the address names the library view to open, and wins over a `?path`.
    if (!openViewFromAddress() && initialPath) {
        folderPathInput.value = initialPath;
        scanFolder(false);
    }
});
