// TagPup's page: the elements it works with, looked up once. A page's modules run after
// the document is parsed, so every element in index.html is there to find.

// Database selection logic
export const dbSelect = document.getElementById('db-select');
export const btnCreateDb = document.getElementById('btn-create-db');
export const btnChangeDb = document.getElementById('btn-change-db');

// DOM Elements
export const folderPathInput = document.getElementById('folder-path-input');
export const btnBrowseFolder = document.getElementById('btn-browse-folder');
export const btnSuggestTags = document.getElementById('btn-suggest-tags');
export const suggestProgressContainer = document.getElementById('suggest-progress-container');
export const suggestProgressBar = document.getElementById('suggest-progress-bar');
export const suggestProgressText = document.getElementById('suggest-progress-text');
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
export const folderViewTitle = document.getElementById('folder-view-title');
export const folderViewStats = document.getElementById('folder-view-stats');
export const btnSelectAllThumbnails = document.getElementById('btn-select-all-thumbnails');
export const btnSelectNoneThumbnails = document.getElementById('btn-select-none-thumbnails');
export const selectedThumbnailsCount = document.getElementById('selected-thumbnails-count');
export const btnFolderAutoApply = document.getElementById('btn-folder-auto-apply');
export const facesStrip = document.getElementById('faces-strip');
export const facesSummary = document.getElementById('faces-summary');
export const folderSelectionSidebar = document.getElementById('folder-selection-sidebar');
export const selectionEmptyHint = document.getElementById('selection-empty-hint');
export const selectionSummaryScroll = document.querySelector('.selection-summary-scroll');
export const selectionSummaryCount = document.getElementById('selection-summary-count');
export const selectionPeopleList = document.getElementById('selection-people-list');
export const selectionTagsList = document.getElementById('selection-tags-list');
export const selectionSuggestedPeopleList = document.getElementById('selection-suggested-people-list');
export const selectionSuggestedTagsList = document.getElementById('selection-suggested-tags-list');
export const selectionDateLabel = document.getElementById('selection-date-label');
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
export const btnRotateLeft = document.getElementById('btn-rotate-left');
export const btnRotateRight = document.getElementById('btn-rotate-right');
export const btnDeletePhoto = document.getElementById('btn-delete-photo');
export const facesSection = document.getElementById('faces-section');

export const detailPath = document.getElementById('detail-path');
export const detailDateTaken = document.getElementById('detail-date-taken');
export const inputPhotoTitle = document.getElementById('input-photo-title');
export const btnSaveTitle = document.getElementById('btn-save-title');
export const btnSaveDetails = document.getElementById('btn-save-details');
export const detailPeople = document.getElementById('detail-people');
export const inputAddPerson = document.getElementById('input-add-person');
export const btnAddPerson = document.getElementById('btn-add-person');
export const detailTags = document.getElementById('detail-tags');
export const inputAddTag = document.getElementById('input-add-tag');
export const btnAddTag = document.getElementById('btn-add-tag');

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

// The grid's right-click menu (grid.js)
export const gridContextMenu = document.getElementById('grid-context-menu');

// The photo, zoomed (photo.js)
export const imageZoom = document.getElementById('image-zoom');
export const imageZoomImg = document.getElementById('image-zoom-img');

// Date Taken Editor Dialog Controls
export const btnEditDateTaken = document.getElementById('btn-edit-date-taken');
export const dateTakenModal = document.getElementById('date-taken-modal');
export const btnCloseDateModal = document.getElementById('btn-close-date-modal');
export const btnCancelDateModal = document.getElementById('btn-cancel-date-modal');
export const btnSaveDateModal = document.getElementById('btn-save-date-modal');
export const inputDateTaken = document.getElementById('input-date-taken');

// Taxonomy Tree Manager Controls & Resolution Dialogs
export const btnManageTaxonomy = document.getElementById('btn-manage-taxonomy');
export const taxonomyModal = document.getElementById('taxonomy-modal');
export const btnCloseTaxonomy = document.getElementById('btn-close-taxonomy');
export const btnCloseTaxonomyFooter = document.getElementById('btn-close-taxonomy-footer');
export const btnTaxonomyAddRoot = document.getElementById('btn-taxonomy-add-root');
export const taxonomySearchInput = document.getElementById('taxonomy-search-input');
