// The TagPup page: its start-up, and the wiring of each feature's module to the page.
// Every request goes through api.js, which puts the library in front of it
// (web/common/api.js).
import { initDatabaseSelector } from './common/library.js';
import { upper } from './hooks.js';
import {
    btnAddPerson, btnAddTag, btnApplyAllSingleSugg, btnApplyTimeshift, btnBrowseFolder,
    btnBulkAddPeople, btnBulkAddTags, btnCarryForward, btnCreateDb, btnDeletePhoto,
    btnFolderAutoApply, btnRefreshList, btnRotateLeft, btnRotateRight, btnSaveTitle,
    btnSelectAllThumbnails, btnSelectNoneThumbnails, btnSuggestTags, btnSuggestTitleWand,
    btnUndo, bulkAddPeopleInput, bulkAddTagsInput, dbSelect, detailPath, folderPathInput,
    folderViewHeader, inputAddPerson, inputAddTag, inputPhotoTitle, mainImage, photoSearch
} from './elements.js';
import { fetchKnownTagsAndPeople } from './tags.js';
import { leavePhotoThen, wireUnsavedEdits } from './edits.js';
import {
    browseFolder, filterFileList, renderFileList, scanFolder, showFolderView,
    wireChangeDogPark, wireFolderPathInput, wireSidebarResizer
} from './folder.js';
import { wireTaxonomyModal } from './taxonomy.js';
import {
    carryTagsForward, deleteActivePhoto, openPhotoInDefaultApp, renderTags, rotatePhoto,
    saveSingleAddPerson, saveSingleAddTag, saveSingleTitle, selectPhoto,
    updateCarryForwardState, wireDateTakenModal, wireZoom
} from './photo.js';
import {
    renderThumbnails, selectAllThumbnails, selectNoneThumbnails, wireGridContextMenu,
    wireThumbnailSize
} from './grid.js';
import { recordUndo, undoLastOperation } from './undo.js';
import { enableSwipeNavigation, wireKeyboard } from './navigation.js';
import {
    bulkAddPeopleToSelection, bulkAddTagsToSelection, updateSelectedThumbnailsCount
} from './selection.js';
import {
    applyAllSingleSuggestions, applyFolderSuggestionsLevel, applySuggestedTagDirect,
    applySuggestedTitle, checkIndexingStatus, checkSuggestionsStatus, renderSuggestionsPanel,
    startSuggestions, updateFolderAutoApplyState, updateSuggestButtonState
} from './suggestions.js';
import {
    applyTimeShift, populateCameraModelsDropdown, updateCameraHighlights,
    wireRenameAndTimeShift
} from './rename.js';

// What a feature calls in a module above it (hooks.js).
Object.assign(upper, {
    applySuggestedTagDirect, checkSuggestionsStatus, populateCameraModelsDropdown, recordUndo,
    renderFileList, renderSuggestionsPanel, renderTags, renderThumbnails, selectPhoto,
    updateCameraHighlights, updateCarryForwardState, updateFolderAutoApplyState,
    updateSelectedThumbnailsCount, updateSuggestButtonState
});

document.addEventListener('DOMContentLoaded', () => {
    // The picker and the library this browser remembers (web/common/library.js).
    // Another dog park is another page. Ask about unsaved edits here, with Save on
    // offer, rather than leave it to the browser's bare "Leave site?"; staying puts
    // the list back on the dog park still open.
    initDatabaseSelector(dbSelect, btnCreateDb, {
        beforeLeaving: (go, stay) => leavePhotoThen(go, { onStay: stay }),
    });

    wireChangeDogPark();

    // Load Datalists on Startup
    fetchKnownTagsAndPeople();

    // Event Listeners setup
    btnBrowseFolder.addEventListener('click', browseFolder);
    wireFolderPathInput();
    btnSuggestTags.addEventListener('click', startSuggestions);
    btnRefreshList.addEventListener('click', () => scanFolder(true));
    
    photoSearch.addEventListener('input', filterFileList);
    folderViewHeader.addEventListener('click', showFolderView);
    
    btnSelectAllThumbnails.addEventListener('click', selectAllThumbnails);
    btnSelectNoneThumbnails.addEventListener('click', selectNoneThumbnails);
    btnBulkAddPeople.addEventListener('click', bulkAddPeopleToSelection);
    btnBulkAddTags.addEventListener('click', bulkAddTagsToSelection);
    bulkAddPeopleInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddPeopleToSelection(); });
    bulkAddTagsInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') bulkAddTagsToSelection(); });
    btnFolderAutoApply.addEventListener('click', applyFolderSuggestionsLevel);
    
    btnRotateLeft.addEventListener('click', () => rotatePhoto('left'));
    btnRotateRight.addEventListener('click', () => rotatePhoto('right'));
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
    detailPath.addEventListener('click', openPhotoInDefaultApp);
    detailPath.title = 'Open in default app';
    btnApplyTimeshift.addEventListener('click', applyTimeShift);
    
    wireThumbnailSize();
    wireRenameAndTimeShift();

    wireKeyboard();

    wireSidebarResizer();

    wireGridContextMenu();

    enableSwipeNavigation(mainImage);

    if (btnCarryForward) btnCarryForward.addEventListener('click', carryTagsForward);
    if (btnUndo) btnUndo.addEventListener('click', undoLastOperation);

    wireZoom();

    wireDateTakenModal();

    wireTaxonomyModal();

    // ---- Start ------------------------------------------------------------
    //
    // Last, deliberately. This opens the folder in the ?path= and picks up an index
    // already running on it, which means it calls into most of the app. Doing that
    // from the middle of this closure reaches `let` bindings declared further down
    // before their declarations have run, and a `let` reached early does not read as
    // undefined -- it throws.
    //
    // It did: checkIndexingStatus touched indexProgressTimer, threw, and took the
    // rest of this closure's body with it, so facesRequestToken was never initialised
    // either. The scan that followed then failed on *that*, and the message said
    // "Error scanning folder: Cannot access 'facesRequestToken' before
    // initialization" -- two removes from the line at fault.
    //
    // Nothing runs the app before this point. tests/frontend/tag-vocabulary.test.mjs
    // keeps it that way.
    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get('path');
    if (initialPath) {
        folderPathInput.value = initialPath;
        scanFolder(false);
        checkIndexingStatus(initialPath);
    }
});
