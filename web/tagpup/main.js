// The TagPup page: its start-up, and the wiring of each feature's module to the page.
// Every request goes through api.js, which puts the library in front of it
// (web/common/api.js).
import { initDatabaseSelector } from './common/library.js';
import { wireRootsBanner } from './common/roots-banner.js';
import { loadRules } from './common/validate.js';
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
import { wireTagPupGear } from './gear.js';
import {
    leaveLibraryView, libraryChanged, openViewFromAddress, refreshFolderOrView, wireLibraryView
} from './library-view.js';
import { checkFolderMembership, wireMembership } from './membership.js';
import { navigatorCountsChanged, navigatorFollows, wireNavigator } from './navigator.js';
import { addedFromView, wireBanner } from './library-banner.js';
import { attachBulk, wireBulk } from './bulk-job.js';
import { landOnAnchor, libraryViewPainted, wireMoves } from './library-moves.js';
import { checkDamagedPhotos, showLibraryDamage } from './damaged.js';
import {
    carryTagsForward, deleteActivePhoto, openPhotoInDefaultApp, renderTags, rotatePhoto,
    reloadChangedPhoto, saveSingleAddPerson, saveSingleAddTag, saveSingleTitle, selectPhoto,
    updateCarryForwardState, wireDateTakenModal, wireZoom
} from './photo.js';
import {
    renderThumbnails, selectAllThumbnails, selectNoneThumbnails, wireGridContextMenu,
    wireThumbnailGrid, wireThumbnailSize
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
    addedFromView, applySuggestedTagDirect, checkDamagedPhotos, checkFolderMembership, checkSuggestionsStatus, populateCameraModelsDropdown, recordUndo,
    renderFileList, renderSuggestionsPanel, renderTags, renderThumbnails, selectPhoto,
    landOnAnchor, leaveLibraryView, libraryChanged, libraryViewPainted, navigatorCountsChanged, navigatorFollows,
    reloadChangedPhoto,
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
        beforeLeaving: (go, stay) => leavePhotoThen(go, { onStay: stay }),
    });

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
    btnRefreshList.addEventListener('click', refreshFolderOrView);
    
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
    
    wireThumbnailGrid();
    wireThumbnailSize();
    wireRenameAndTimeShift();

    wireKeyboard();

    wireSidebarResizer();

    wireGridContextMenu();

    // Views of the library: the strip above the grid, and Back and Forward between views and folders.
    wireLibraryView();

    // The navigator beside the grid, the move between a folder and its view, and the banner for photos the library lacks.
    wireNavigator();
    wireMoves();
    wireBanner();

    // The strip of a bulk edit of the library's photos: Cancel, Resume, Start again.
    wireBulk();

    enableSwipeNavigation(mainImage);

    if (btnCarryForward) btnCarryForward.addEventListener('click', carryTagsForward);
    if (btnUndo) btnUndo.addEventListener('click', undoLastOperation);

    wireZoom();

    wireDateTakenModal();

    // The gear: the tag editor (web/common/tag-editor.js) and TagTuner on this library.
    wireTagPupGear();

    // A library whose root this computer does not place says so, at the top of the page.
    wireRootsBanner();

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
    // How many of the library's photos were found damaged, in the header (damaged.js).
    showLibraryDamage();
    // A bulk edit already running in this library (started before this page was opened or reloaded) is picked up by its strip.
    attachBulk();
    const params = new URLSearchParams(window.location.search);
    const initialPath = params.get('path');
    // A `?view` in the address names the library view to open, and wins over a `?path`.
    if (!openViewFromAddress() && initialPath) {
        folderPathInput.value = initialPath;
        scanFolder(false);
        checkIndexingStatus(initialPath);
    }
});
