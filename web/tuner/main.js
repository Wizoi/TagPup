// The TagTuner page: start-up and wiring. Each feature is a module beside this one,
// its state in state.js; every request goes through api.js, which puts the library
// in front of it (web/common/api.js).
import { initDatabaseSelector } from './common/library.js';
import { loadRules } from './common/validate.js';
import { state } from './state.js';
import { modeSelect, showMatchedToggle } from './elements.js';
import { upper } from './hooks.js';
import { fetchKnownPeople } from './shared.js';
import { wireTags } from './tags.js';
import { wireSelection } from './selection.js';
import { wireNewPerson } from './new-person.js';
import { wireAssign } from './assign.js';
import { wireGrid } from './grid.js';
import { fetchPeopleWithCounts, selectPerson, wirePeople } from './people.js';
import { selectPhoto, wireFacesStrip } from './faces-strip.js';
import {
    fetchPhotos, refreshSidebarQuietly, updateMatchedToggleVisibility, wireSidebar,
} from './sidebar.js';
import { restoreIndexingState, wireIndexing } from './indexing.js';
import { wireTunerGear } from './gear.js';

document.addEventListener('DOMContentLoaded', () => {
    // What may be set, as the server says (web/common/validate.js): once.
    loadRules();
    // What the features call above themselves (hooks.js).
    Object.assign(upper, { selectPhoto, fetchPeopleWithCounts, selectPerson, refreshSidebarQuietly });

    // Database selection logic: the picker and the library this browser remembers
    // (web/common/library.js).
    const dbSelect = document.getElementById('db-select');
    const btnCreateDb = document.getElementById('btn-create-db');
    initDatabaseSelector(dbSelect, btnCreateDb);

    // Load initial data
    const urlParams = new URLSearchParams(window.location.search);
    const urlMode = urlParams.get('mode');
    if (urlMode && (urlMode === 'folder-match' || urlMode === 'face-matching' || urlMode === 'unmatched-faces')) {
        modeSelect.value = urlMode;
    }

    const urlPhoto = urlParams.get('photo');
    if (urlPhoto) {
        state.activePhotoPath = urlPhoto;
    }

    const urlPerson = urlParams.get('person');
    if (urlPerson) {
        state.activePersonName = urlPerson;
    }

    const urlShowMatched = urlParams.get('show_matched');
    if (urlShowMatched === 'true' && showMatchedToggle) {
        showMatchedToggle.checked = true;
    }
    updateMatchedToggleVisibility();

    fetchPhotos();
    fetchKnownPeople();
    restoreIndexingState();

    // Each feature's listeners, in the order the page first added them.
    wireSidebar();
    wireFacesStrip();
    wireAssign();
    wireNewPerson();
    wireSelection();
    wireGrid();
    wireIndexing();
    wirePeople();
    wireTags();
    // The gear: the tag editor (web/common/tag-editor.js), the library's settings and
    // TagPup on this library.
    wireTunerGear();
});
