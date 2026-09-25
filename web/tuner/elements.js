// The page's elements that more than one feature reaches.
// DOM Elements
export const modeSelect = document.getElementById('tuner-mode');
export const photoSearch = document.getElementById('photo-search');
export const photoList = document.getElementById('photo-list');
export const listStats = document.getElementById('list-stats');
export const peopleSort = document.getElementById('people-sort');
export const emptyState = document.getElementById('empty-state');
export const panelContent = document.getElementById('panel-content');

export const showMatchedToggle = document.getElementById('show-matched-toggle');

// Face Matching Mode DOM Elements
export const faceMatchingContent = document.getElementById('face-matching-content');

// Declared here with the other elements rather than beside the code that uses
// them. fetchPhotos() runs during startup and hides this panel, so a `const`
// further down would be reached before its line had run -- which throws, and
// takes the rest of this closure with it.
export const tagViewContent = document.getElementById('tag-view-content');

export const matchingPersonCount = document.getElementById('matching-person-count');
export const btnUnmatchSelected = document.getElementById('btn-unmatch-selected');
export const matchingFacesGrid = document.getElementById('matching-faces-grid');
export const inputReassignName = document.getElementById('input-reassign-name');
export const btnReassignSelected = document.getElementById('btn-reassign-selected');
export const btnNewPerson = document.getElementById('btn-new-person');
export const btnRenamePerson = document.getElementById('btn-rename-person');

export const btnExcludeSelected = document.getElementById('btn-exclude-selected');
export const btnRestoreSelected = document.getElementById('btn-restore-selected');

export const tabMatches = document.getElementById('tab-matches');
export const tabOutliers = document.getElementById('tab-outliers');
export const tabLowConf = document.getElementById('tab-low-conf');
