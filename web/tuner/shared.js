// What every feature uses: the address bar, the names the library knows.
import { api } from './common/api.js';
import { state } from './state.js';
import { modeSelect, showMatchedToggle } from './elements.js';

// ------------------------------------------------------------------ paths --
// Every photo and folder path the server sends is already in one spelling -- the
// native absolute path, exactly as the database holds it -- so server paths can
// be compared with `===` and are never rewritten here. pathKey and samePath
// (web/common/paths.js) are for paths from anywhere else: the ?photo= in the URL,
// or what the native Browse dialog returned (forward slashes). tests/frontend/
// path-helpers.test.mjs fails on a separator conversion anywhere else.

export function updateURLParams() {
    const params = new URLSearchParams(window.location.search);
    params.set('mode', modeSelect.value);
    const mode = modeSelect.value;
    if (mode === 'face-matching' || mode === 'unmatched-faces') {
        if (state.activePersonName) {
            params.set('person', state.activePersonName);
        } else {
            params.delete('person');
        }
        params.delete('photo');
        params.delete('show_matched');
    } else {
        if (state.activePhotoPath) {
            params.set('photo', state.activePhotoPath);
        } else {
            params.delete('photo');
        }
        params.delete('person');
        if (showMatchedToggle && showMatchedToggle.checked) {
            params.set('show_matched', 'true');
        } else {
            params.delete('show_matched');
        }
    }
    window.history.replaceState({}, '', `${window.location.pathname}?${params.toString()}`);
}

// Fetch all unique known people for autocomplete
export function fetchKnownPeople() {
    api.fetch('/api/people')
        .then(res => {
            if (!res.ok) throw new Error('Failed to fetch people list');
            return res.json();
        })
        .then(data => {
            state.allKnownPeople = data;
            updatePeopleDatalist();
        })
        .catch(err => console.error('Error fetching people list:', err));
    api.fetch('/api/people?include_hidden=1')
        .then(res => res.ok ? res.json() : [])
        .then(data => { state.everyKnownPerson = Array.isArray(data) ? data : []; })
        .catch(err => console.error('Error fetching people list:', err));
}

/** Is this somebody the library already knows, hidden from autocomplete or not? */
export function personExists(name) {
    return state.everyKnownPerson.includes(name) || state.allKnownPeople.includes(name);
}

// Update global datalist elements with known people names
function updatePeopleDatalist() {
    const datalist = document.getElementById('people-datalist');
    if (!datalist) return;
    datalist.innerHTML = '';
    state.allKnownPeople.forEach(person => {
        const option = document.createElement('option');
        option.value = person;
        datalist.appendChild(option);
    });
}
