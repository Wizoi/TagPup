// TagPup's page: the elements it works with, looked up once. A page's modules run after
// the document is parsed, so every element in index.html is there to find.

// Database selection logic
export const dbSelect = document.getElementById('db-select');
export const btnCreateDb = document.getElementById('btn-create-db');
export const btnChangeDb = document.getElementById('btn-change-db');
// The library the page works in, in the header (membership.js)
export const libraryNameBadge = document.getElementById('library-name');

// DOM Elements
export const folderPathInput = document.getElementById('folder-path-input');
export const btnBrowseFolder = document.getElementById('btn-browse-folder');
export const btnSuggestTags = document.getElementById('btn-suggest-tags');
export const suggestProgressContainer = document.getElementById('suggest-progress-container');
export const suggestProgressBar = document.getElementById('suggest-progress-bar');
export const suggestProgressText = document.getElementById('suggest-progress-text');
export const btnSuggestCancel = document.getElementById('btn-suggest-cancel');
export const indexProgressContainer = document.getElementById('index-progress-container');
export const indexProgressBar = document.getElementById('index-progress-bar');
export const indexProgressText = document.getElementById('index-progress-text');

export const photoSearch = document.getElementById('photo-search');
export const photoList = document.getElementById('photo-list');
export const photoPosition = document.getElementById('photo-position');   // "12 of 48"
export const listStats = document.getElementById('list-stats');
export const currentFolderName = document.getElementById('current-folder-name');
export const btnRefreshList = document.getElementById('btn-refresh-list');

export const sidebar = document.querySelector('.sidebar');
export const sidebarResizer = document.getElementById('sidebar-resizer');

export const detailsPanel = document.getElementById('details-panel');
export const emptyState = document.getElementById('empty-state');
export const panelContent = document.getElementById('panel-content');

export const folderViewHeader = document.getElementById('folder-view-header');
export const folderViewContent = document.getElementById('folder-view-content');
// What scrolls: the grid sits in it below the folder's header (vgrid.js windows by its scroll).
export const folderViewMain = document.getElementById('folder-view-main');
export const folderViewTitle = document.getElementById('folder-view-title');
export const folderViewStats = document.getElementById('folder-view-stats');
export const btnDetailsToggle = document.getElementById('btn-details-toggle');
export const btnDetailsClose = document.getElementById('btn-details-close');
export const btnSelectAllThumbnails = document.getElementById('btn-select-all-thumbnails');
export const btnSelectNoneThumbnails = document.getElementById('btn-select-none-thumbnails');
export const btnDeleteSelection = document.getElementById('btn-delete-selection');
// The photo panel's way back to the grid of a library view (view-left.js, #780)
export const btnPhotoBack = document.getElementById('btn-photo-back');
export const selectedThumbnailsCount = document.getElementById('selected-thumbnails-count');
export const btnFolderAutoApply = document.getElementById('btn-folder-auto-apply');
export const facesStrip = document.getElementById('faces-strip');
export const btnAddAllFaces = document.getElementById('btn-add-all-faces');
export const facesSummary = document.getElementById('faces-summary');
export const folderSelectionSidebar = document.getElementById('folder-selection-sidebar');
export const selectionEmptyHint = document.getElementById('selection-empty-hint');
export const selectionSummaryScroll = document.querySelector('.selection-summary-scroll');
export const selectionSummaryCount = document.getElementById('selection-summary-count');
export const selectionPeopleList = document.getElementById('selection-people-list');
export const selectionTagsList = document.getElementById('selection-tags-list');
export const selectionSuggestedPeopleList = document.getElementById('selection-suggested-people-list');
export const selectionSuggestedTagsList = document.getElementById('selection-suggested-tags-list');
export const selectionNote = document.getElementById('selection-note');
export const selectionDateGroup = document.getElementById('selection-date-group');
export const selectionDateLabel = document.getElementById('selection-date-label');
export const selectionFoldersGroup = document.getElementById('selection-folders-group');
export const selectionFoldersList = document.getElementById('selection-folders-list');
// The panel's two halves (selection-panel.js, #781)
export const selectionNavigation = document.getElementById('selection-navigation');
export const selectionPeopleJump = document.getElementById('selection-people-jump');
export const selectionKeywordJump = document.getElementById('selection-keyword-jump');
export const btnSelectionTagging = document.getElementById('btn-selection-tagging');
export const selectionTaggingBody = document.getElementById('selection-tagging-body');
export const selectionAutoApply = document.getElementById('selection-auto-apply');
export const selectionDateValue = document.getElementById('selection-date-value');
export const bulkAddPeopleInput = document.getElementById('bulk-add-people-input');
export const btnBulkAddPeople = document.getElementById('btn-bulk-add-people');
export const bulkAddTagsInput = document.getElementById('bulk-add-tags-input');
export const btnBulkAddTags = document.getElementById('btn-bulk-add-tags');
export const thumbnailsGrid = document.getElementById('thumbnails-grid');

export const btnSizeSmall = document.getElementById('btn-size-small');
export const btnSizeMedium = document.getElementById('btn-size-medium');
export const btnSizeLarge = document.getElementById('btn-size-large');

export const timeshiftPanel = document.getElementById('timeshift-panel');
export const timeshiftCameraSelect = document.getElementById('timeshift-camera-select');
export const timeshiftMinutesInput = document.getElementById('timeshift-minutes-input');
export const btnApplyTimeshift = document.getElementById('btn-apply-timeshift');
export const btnToggleTimeshift = document.getElementById('btn-toggle-timeshift');

export const btnToggleRename = document.getElementById('btn-toggle-rename');
export const renamePanel = document.getElementById('rename-panel');
export const renameGroupingInput = document.getElementById('rename-grouping-input');
export const btnApplyRename = document.getElementById('btn-apply-rename');

export const mainImage = document.getElementById('main-image');
export const mainVideo = document.getElementById('main-video');
export const imageViewer = document.getElementById('image-viewer');
export const faceLayer = document.getElementById('face-layer');
export const btnRotateLeft = document.getElementById('btn-rotate-left');
export const btnRotateRight = document.getElementById('btn-rotate-right');
export const btnMirror = document.getElementById('btn-mirror');
export const btnDeletePhoto = document.getElementById('btn-delete-photo');
// The clip panel's Convert, Make smaller (MP4), the line of one under way and its Cancel (clip-convert.js).
export const btnClipConvert = document.getElementById('btn-clip-convert');
export const btnClipShrink = document.getElementById('btn-clip-shrink');
export const clipConvertNote = document.getElementById('clip-convert-note');
export const btnClipConvertCancel = document.getElementById('btn-clip-convert-cancel');
export const facesSection = document.getElementById('faces-section');

// Where the open photo is: its folder's name, its own, and the button that copies its path (photo-path.js).
export const detailFolder = document.getElementById('detail-folder');
export const detailFileName = document.getElementById('detail-file-name');
export const btnCopyPath = document.getElementById('btn-copy-path');
// Smart Rename of the open photo: the grouping box beside its name and its button (rename-one.js).
export const inputSmartRenameOne = document.getElementById('input-smart-rename-one');
export const btnSmartRenameOne = document.getElementById('btn-smart-rename-one');
export const detailSize = document.getElementById('detail-size');
export const detailDateTaken = document.getElementById('detail-date-taken');
export const detailCamera = document.getElementById('detail-camera');
export const detailCameraItem = document.getElementById('detail-camera-item');
export const inputPhotoTitle = document.getElementById('input-photo-title');
export const btnSaveTitle = document.getElementById('btn-save-title');
export const btnSaveDetails = document.getElementById('btn-save-details');
export const writeQueueBox = document.getElementById('write-queue');

// The bulk edit's strip (bulk-strip.js)
export const detailPeople = document.getElementById('detail-people');
export const inputAddPerson = document.getElementById('input-add-person');
export const btnAddPerson = document.getElementById('btn-add-person');
export const detailTags = document.getElementById('detail-tags');
export const inputAddTag = document.getElementById('input-add-tag');
export const btnAddTag = document.getElementById('btn-add-tag');
// The add boxes of the single photo's people and keywords, shown by their pencils (edit-toggles.js).
export const btnEditPeople = document.getElementById('btn-edit-people');
export const addPeopleGroup = document.getElementById('add-people-group');
export const btnEditTags = document.getElementById('btn-edit-tags');
export const addTagsGroup = document.getElementById('add-tags-group');

export const suggestionsSection = document.getElementById('suggestions-section');
export const btnApplyAllSingleSugg = document.getElementById('btn-apply-all-single-sugg');
export const btnSuggestTitleWand = document.getElementById('btn-suggest-title-wand');
export const suggestedPeopleContainer = document.getElementById('suggested-people-container');
export const suggestedTagsContainer = document.getElementById('suggested-tags-container');

export const statusDot = document.getElementById('status-dot');
export const statusText = document.getElementById('status-text');
export const btnUndo = document.getElementById('btn-undo');
export const btnCarryForward = document.getElementById('btn-carry-forward');

export const tagsDatalist = document.getElementById('tags-datalist');
export const peopleDatalist = document.getElementById('people-datalist');

// The sidebar's two panes and the switch between them (navigator.js)
export const sidebarSwitch = document.getElementById('sidebar-switch');
export const sidebarTabLibrary = document.getElementById('sidebar-tab-library');
export const sidebarTabFolder = document.getElementById('sidebar-tab-folder');
export const sidebarPaneLibrary = document.getElementById('sidebar-pane-library');
export const sidebarPaneFolder = document.getElementById('sidebar-pane-folder');

// Moving between a folder and its view of the library (library-moves.js), and when the library was last in step (sync-state.js)
export const btnShowInLibrary = document.getElementById('btn-show-in-library');
// Name faces from tags, the open folder's (name-faces.js), shown with Show in library (folder.js)
export const btnNameFaces = document.getElementById('btn-name-faces');
export const libraryStripSync = document.getElementById('library-strip-sync');
export const movesBanner = document.getElementById('moves-banner');
export const movesBannerText = document.getElementById('moves-banner-text');
export const btnMovesAdd = document.getElementById('btn-moves-add');
export const btnMovesDismiss = document.getElementById('btn-moves-dismiss');

// A photo whose file is gone is shown and not editable (stale.js)
export const photoMissingNote = document.getElementById('photo-missing-note');

// The grid's right-click menu (grid.js)
export const gridContextMenu = document.getElementById('grid-context-menu');

// Date Taken Editor Dialog Controls
export const btnEditDateTaken = document.getElementById('btn-edit-date-taken');
export const dateTakenModal = document.getElementById('date-taken-modal');
export const btnCloseDateModal = document.getElementById('btn-close-date-modal');
export const btnCancelDateModal = document.getElementById('btn-cancel-date-modal');
export const btnSaveDateModal = document.getElementById('btn-save-date-modal');
export const dateTakenFile = document.getElementById('date-taken-file');
export const dateTakenNote = document.getElementById('date-taken-note');
export const inputDateTaken =document.getElementById('input-date-taken');

// Add this folder to the library? (membership.js)
export const addFolderModal = document.getElementById('add-folder-modal');
export const addFolderTitle = document.getElementById('add-folder-title');
export const addFolderLibrary = document.getElementById('add-folder-library');
export const addFolderPath = document.getElementById('add-folder-path');
export const addFolderFacts = document.getElementById('add-folder-facts');
export const btnAddFolder = document.getElementById('btn-add-folder');
export const btnJustLook = document.getElementById('btn-just-look');
export const justLookingNote = document.getElementById('just-looking-note');
// The top of the grid, one floating header in a library view (#713), and its strip that says which view is open (library-view.js)
export const folderViewTop = document.getElementById('folder-view-top');
export const libraryStrip = document.getElementById('library-strip');
export const libraryStripSource = document.getElementById('library-strip-source');
export const libraryStripTotal = document.getElementById('library-strip-total');
export const libraryStripStatus = document.getElementById('library-strip-status');
export const filterBar = document.getElementById('filter-bar');
export const filterBarChips = document.getElementById('filter-bar-chips');
export const btnFilterBarClear = document.getElementById('btn-filter-bar-clear');
export const btnLibraryRefresh = document.getElementById('btn-library-refresh');
// The search box of the Library pane (search.js, phase 9e-2)
export const librarySearchWords = document.getElementById('library-search-words');
export const btnLibrarySearch = document.getElementById('btn-library-search');
export const btnLibrarySearchClear = document.getElementById('btn-library-search-clear');
export const btnLibrarySearchFilters = document.getElementById('btn-library-search-filters');
export const librarySearchFilters = document.getElementById('library-search-filters');
export const librarySearchWithin = document.getElementById('library-search-within');
export const librarySearchWithinRow = document.getElementById('library-search-within-row');
export const librarySearchWithinName = document.getElementById('library-search-within-name');
export const librarySearchNote = document.getElementById('library-search-note');
// Sort by, in a view's header, and its menu (sort-menu.js, #714)
export const btnSortBy = document.getElementById('btn-sort-by');
export const sortByCurrent = document.getElementById('sort-by-current');
export const sortMenu = document.getElementById('sort-menu');
// Show, beside it, and its menu (filter-menu.js)
export const btnFilterMedia = document.getElementById('btn-filter-media');
export const filterMediaCurrent = document.getElementById('filter-media-current');
export const filterMenu = document.getElementById('filter-menu');
export const justLookingText = document.getElementById('just-looking-text');
export const btnAddFolderFromNote = document.getElementById('btn-add-folder-from-note');
