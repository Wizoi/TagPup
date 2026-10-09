// What every feature uses: the address bar, the names the library knows.
import { api } from './common/api.js';
import { choosePerson } from './common/person-choice.js';
import { GROUP_SEPARATOR, PeopleDirectory, personLabel, personTitle, sortedTags } from './common/vocabulary.js';
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
        if (state.activePersonName && state.activePersonId !== null) {
            params.set('person_id', String(state.activePersonId));
        } else {
            params.delete('person_id');
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
    // The people with a tag, by id: who a typed name is, and the label of each (identity by id).
    api.fetch('/api/people?records=1&include_hidden=1')
        .then(res => res.ok ? res.json() : [])
        .then(data => {
            state.people = new PeopleDirectory(data);
            updatePeopleDatalist();
        })
        .catch(err => console.error('Error fetching the people records:', err));
}

/**
 * Who typed text means: the person (their id with them), after asking which when two people have the name, or a name no tag has.
 * Resolves to {person}, or {name, exists} -- `exists`: faces or photos already hold the name (hidden from autocomplete, or no tag
 * yet), so it is not a new person -- or null when nothing was typed or the question was declined. A label (`Sam · Pets`) that
 * finds nobody is refused, never made a person's name: the people have not been read yet.
 */
export function resolveTyped(text, title = 'Which person?') {
    const found = state.people.match(text);
    if (found.kind === 'person') return Promise.resolve({ person: found.person });
    if (found.kind === 'new') {
        if (found.name.includes(GROUP_SEPARATOR)) {
            alert(`"${found.name}" is a person's label, and the page has not read the people yet: try again in a moment.`);
            return Promise.resolve(null);
        }
        return Promise.resolve({
            name: found.name, exists: state.everyKnownPerson.includes(found.name) || state.allKnownPeople.includes(found.name),
        });
    }
    if (found.kind === 'choose') {
        return choosePerson(found.people, {
            title, about: `${found.people.length} people are called ${found.people[0].name}.`,
        }).then(person => (person ? { person } : null));
    }
    return Promise.resolve(null);
}

/** The label of the person a list row names (its nested `person`, else the id looked up, else the name). */
export function labelOfPerson(name, personId = null, person = null) {
    const found = person || (personId !== null && personId !== undefined ? state.people.ofId(personId) : null);
    return found ? personLabel(found) : String(name ?? '');
}

/** The person a face's suggestion names, as a request names them: {id, name} (the id when the server sent it). */
export function suggestedWho(face) {
    const id = face.suggested_person_id !== undefined ? face.suggested_person_id : null;
    const known = id !== null ? state.people.ofId(id) : null;
    return known || { id, name: face.suggested_name };
}

/** What shows for a face's suggested person: their label (`Sam · Pets` where a name is shared). */
export function suggestedLabel(face) {
    return labelOfPerson(face.suggested_name, face.suggested_person_id !== undefined ? face.suggested_person_id : null);
}

/** The hover text for the same: the full tag. */
export function titleOfPerson(name, personId = null, person = null) {
    const found = person || (personId !== null && personId !== undefined ? state.people.ofId(personId) : null);
    return found ? personTitle(found) : String(name ?? '');
}

// Update global datalist elements with known people: a name, or the label of each of two people who share it.
function updatePeopleDatalist() {
    const datalist = document.getElementById('people-datalist');
    if (!datalist) return;
    datalist.innerHTML = '';
    const offered = new Map();
    // A person with a tag is offered by their label; a name only faces hold (no tag) by the name.
    for (const person of state.people.all()) offered.set(personLabel(person), person);
    const named = new Set(state.people.all().map(person => person.name.toLowerCase()));
    for (const name of state.allKnownPeople) {
        if (!named.has(String(name).toLowerCase())) offered.set(name, null);
    }
    sortedTags(offered.keys()).forEach(text => {
        const option = document.createElement('option');
        option.value = text;
        const person = offered.get(text);
        if (person) option.title = personTitle(person);
        datalist.appendChild(option);
    });
}
