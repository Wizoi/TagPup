// TagPup's page: the library's tags and people -- what they are, the lists the fields
// offer, and turning what someone typed into a tag, asking where to file a new one.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { PeopleDirectory, leafOf, rootOf, samePerson, sortedTags, tagProblem } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { isJustLooking } from './looking.js';
import { peopleDatalist, tagsDatalist } from './elements.js';

// Dynamic Autocomplete loaders
export function fetchKnownTagsAndPeople() {
    loadTaxonomy().then(() => {
        api.json('/api/tags')
            .then(data => {
                state.knownTags = Array.isArray(data) ? data : [];
                updateTagsDatalist();
                updatePeopleDatalist();
            })
            .catch(err => console.error("Error loading tags taxonomy:", err));

        // The ones offered while typing: those not hidden from autocomplete.
        api.json('/api/people?records=1')
            .then(data => {
                state.offeredPeople = new PeopleDirectory(data);
                updatePeopleDatalist();
            })
            .catch(err => console.error("Error loading the people offered:", err));
    }).catch(err => console.error("Error loading taxonomy tree:", err));
}

export function namesAPerson(tag) {
    // A person is a node of the tree with no node under it, as the people records say (PeopleDirectory): a face root, a
    // group, or a keyword whose leaf is a face's name is none (docs/findings.md, #1358). The roots' has_face flag and the
    // names given to faces both said yes to those.
    return Boolean(state.people.ofText(tag));
}

// ---- Talking about a tag ------------------------------------------------
//
// A person has two shapes in this app and they are not interchangeable. Their
// identity is a leaf -- "Hazel Brookmire" -- which is what the faces table, the
// suggester and photo.people all speak in. Their tag is a path --
// "People/Hazel Brookmire" -- which is what the keywords must hold and what the
// server matches on, exactly.
//
// Every bug in this area has been a site that converted between the two by hand
// and got it slightly wrong, and they all look different on the surface:
//
//   - clicking a recognised face added the person a second time, bare, because
//     the duplicate check compared a leaf against a path;
//   - the x on a selection chip removed nothing, because it sent the leaf to a
//     server that removes by exact match -- and rewrote every selected file to
//     achieve it;
//   - a suggested "Activity/Cross Country" was written as "Cross Country",
//     because the same line that reduced people to leaves reduced keywords too.
//
// So the conversions live in one place: leafOf, rootOf, samePerson and
// photoAlreadyHas in web/common/vocabulary.js, with this page's own normalizeTag
// below. `tests/frontend/tag-vocabulary.test.mjs` fails on a raw
// `.split('/')` anywhere else, the same way tests/test_db_access.py fails on a raw
// sqlite3.connect.

/**
 * The one spelling of a tag, as the server stores it: each level trimmed.
 * " People / Hazel Brookmire " -> "People/Hazel Brookmire". Only for a tag that
 * tagProblem allows; the cases are in tests/validation_cases.json with the server's.
 */
export function normalizeTag(tag) {
    return String(tag || '').split('/').map(level => level.trim()).filter(Boolean).join('/');
}

/**
 * Of two tags naming one person, the one to keep.
 *
 * The pathed form wins: it says where the person belongs, and a bare name is what
 * you get when that was lost.
 */
export function preferPathed(a, b) {
    if (!a) return b;
    if (!b) return a;
    if (a.includes('/') === b.includes('/')) return a;
    return a.includes('/') ? a : b;
}

export function updateTagsDatalist() {
    tagsDatalist.innerHTML = '';
    
    const uniqueFolderTags = new Set();
    state.folderPhotos.forEach(p => {
        if (p.tags) p.tags.forEach(t => {
            if (!namesAPerson(t)) {
                uniqueFolderTags.add(t);
            }
        });
    });

    const filteredKnownTags = state.knownTags.filter(t => {
        return !namesAPerson(t);
    });

    const combined = sortedTags(new Set([...filteredKnownTags, ...uniqueFolderTags]));
    combined.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t;
        tagsDatalist.appendChild(opt);
    });
    upper.updateFolderAutoApplyState();
}

export function updatePeopleDatalist() {
    peopleDatalist.innerHTML = '';
    
    const peopleSet = new Set();
    
    // Add all person tags from knownTags
    state.knownTags.forEach(t => {
        if (namesAPerson(t)) {
            peopleSet.add(t);
        }
    });
    
    // Every person offered while typing, by their tag: two called alike are two entries.
    state.offeredPeople.all().forEach(person => peopleSet.add(person.tag));

    // Add from folderPhotos. A name alone is no person (a person is a tag path, held as the id of a node): only the tags count.
    state.folderPhotos.forEach(p => {
        if (p.tags) {
            p.tags.forEach(t => {
                if (namesAPerson(t)) {
                    peopleSet.add(t);
                }
            });
        }
    });

    // One entry per person, whatever shape their tag arrived in. The list is
    // built from three sources -- person tags, the people offered, and the tags on the
    // photos in view -- and a person recorded both as "Josephine Sandoval" and as
    // "People/Josephine Sandoval" appeared twice, which is a choice nobody can make
    // correctly because both do the same thing.
    //
    // The pathed form wins: it says where the person belongs, and a bare name is
    // what you get when that was lost.
    //
    // One entry per PERSON, not per leaf: a tag the people records know is that person's id, so two people called Sam are two
    // entries; a bare name one person has is that person; a text no record knows is its own path (the leaf is never identity).
    const byPerson = new Map();
    Array.from(peopleSet).forEach(tag => {
        const leaf = leafOf(tag).toLowerCase();
        if (!leaf) return;
        const known = state.people.ofText(tag);
        const key = known ? `id:${known.id}` : `tag:${tag.trim().toLowerCase()}`;
        byPerson.set(key, preferPathed(byPerson.get(key), tag));
    });

    sortedTags(byPerson.values()).forEach(p => {
        const opt = document.createElement('option');
        opt.value = p;
        peopleDatalist.appendChild(opt);
    });
}

/**
 * The tag tree, and the people in it: read together, so a person filed a moment ago (resolveTagOrPerson creates the node,
 * then reads the tree) is a person at once (namesAPerson asks the people, not the tree).
 */
export function loadTaxonomy() {
    // The people with a tag, by id: a tag path names exactly one, and two called alike are two (identity by id). A failed
    // read is nobody, as in TagTuner (PeopleDirectory lets an error's answer go).
    const people = api.json('/api/people?records=1&include_hidden=1')
        .then(data => { state.people = new PeopleDirectory(data); })
        .catch(err => console.error('Could not load the people records:', err));
    const tree = api.json('/api/taxonomy/tree')
        .then(data => {
            // An error reply is an object, not the list of nodes. Storing it
            // raw made the next tag you typed throw a TypeError out of
            // resolveTagOrPerson, so the add just did nothing. Callers already
            // guard with Array.isArray in places, which is the same bug noticed
            // once and patched at the wrong end.
            state.taxonomyNodes = Array.isArray(data) ? data : [];
        })
        .catch(err => {
            console.error('Could not load the taxonomy:', err);
            state.taxonomyNodes = [];
        });
    return Promise.all([tree, people]);
}

export function showPlacementModal(title, message, options, allowNewRoot = false) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        
        // The typed text and the tag paths go in as text: a tag holding "<" or a quote
        // was markup here, and broke the radio button's value.
        const optionLabel = (value, text, checked) =>
            buildElement('label', { className: 'placement-option-label' }, [
                buildElement('input', { attrs: { type: 'radio', name: 'placement-opt', value, checked } }),
                buildElement('span', { text }),
            ]);
        // Alphabetical, once; the first is the one chosen to begin with.
        const choices = sortedTags(options).map((opt, idx) => optionLabel(opt, opt, idx === 0));
        if (allowNewRoot) {
            choices.push(
                optionLabel('__new_root__', 'Create a new root category...', false),
                buildElement('div', {
                    id: 'new-root-input-container',
                    style: 'display: none; padding-left: 24px; margin-top: 8px; flex-direction: column; gap: 8px;',
                }, [
                    buildElement('input', {
                        id: 'new-root-name-input', className: 'taxonomy-search-input',
                        attrs: { type: 'text', placeholder: 'New root category name...' },
                    }),
                    buildElement('label', {
                        style: 'display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-secondary);',
                    }, [
                        buildElement('input', { id: 'new-root-has-face-checkbox', attrs: { type: 'checkbox' } }),
                        buildElement('span', { text: 'Enable face matching (People/Pets)' }),
                    ]),
                ]),
            );
        }

        overlay.appendChild(buildElement('div', { className: 'modal-container', style: 'max-width: 450px;' }, [
            buildElement('div', { className: 'modal-header' }, [
                buildElement('h2', { text: title }),
                buildElement('button', { className: 'modal-close-btn', text: '\u00d7' }),
            ]),
            buildElement('div', { className: 'modal-body' }, [
                buildElement('p', {
                    style: 'margin-bottom: 16px; color: var(--text-secondary); line-height: 1.5; font-size: 14px;',
                    text: message,
                }),
                buildElement('div', { className: 'placement-options' }, choices),
            ]),
            buildElement('div', { className: 'modal-footer' }, [
                buildElement('button', { className: 'btn btn-secondary btn-cancel', text: 'Cancel' }),
                buildElement('button', { className: 'btn btn-primary btn-confirm', text: 'Confirm' }),
            ]),
        ]));

        document.body.appendChild(overlay);
        
        const radioNewRoot = overlay.querySelector('input[value="__new_root__"]');
        const newRootContainer = overlay.querySelector('#new-root-input-container');
        
        overlay.querySelectorAll('input[name="placement-opt"]').forEach(rad => {
            rad.addEventListener('change', (e) => {
                if (newRootContainer) {
                    newRootContainer.style.display = e.target.value === '__new_root__' ? 'flex' : 'none';
                }
            });
        });
        
        const close = (value) => {
            overlay.className = 'modal-overlay';
            setTimeout(() => overlay.remove(), 300);
            resolve(value);
        };
        
        overlay.querySelector('.modal-close-btn').onclick = () => close(null);
        overlay.querySelector('.btn-cancel').onclick = () => close(null);
        
        overlay.querySelector('.btn-confirm').onclick = () => {
            const selected = overlay.querySelector('input[name="placement-opt"]:checked').value;
            if (selected === '__new_root__') {
                const name = overlay.querySelector('#new-root-name-input').value.trim();
                const hasFace = overlay.querySelector('#new-root-has-face-checkbox').checked;
                if (!name) {
                    alert("Please enter a root category name.");
                    return;
                }
                close({ action: 'create_root', name, hasFace });
            } else {
                close({ action: 'select', root: selected });
            }
        };
    });
}

/**
 * Turn typed text into a taxonomy path, asking where it belongs if need be.
 *
 * With `prompt: false` it will not ask: anything that needs a decision returns
 * null instead of opening the placement modal. That is what lets a blur commit
 * the unambiguous cases and leave the rest alone, rather than deciding when to
 * ask by predicting this function's behaviour -- a copy that would drift.
 */
export async function resolveTagOrPerson(inputName, isPersonField = false, { prompt = true } = {}) {
    inputName = inputName.trim();
    if (!inputName) return null;
    // Before anything is created for it: the server would refuse to write it.
    const problem = tagProblem(inputName);
    if (problem) {
        alert(problem);
        return null;
    }

    const askWhereItGoes = (...args) => (prompt ? showPlacementModal(...args) : null);
    
    if (inputName.includes('/')) {
        // Not while just looking: a tag in the tree is the library's, and an edit of files only
        // changes nothing of it. The tag is written as typed and filed; the index reads it later.
        const created = isJustLooking() ? null : await api.json('/api/taxonomy/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: inputName })
        });
        if (created && created.success === false) {
            alert("Error creating tag: " + created.error);
            return null;
        }
        await loadTaxonomy();
        // As the tree spells it. Returning the text as typed wrote
        // "People / Rowan Thackeray" into the photo beside the tree's
        // People/Rowan Thackeray.
        return normalizeTag(inputName);
    }
    
    const allRoots = state.taxonomyNodes.filter(n => n.parent_id === null);
    // A root people are filed under has nodes under it (an empty one is a person of the top level), or is a new library's People.
    const peopleRoots = allRoots.filter(r => r.has_face === 1
        && (r.name.toLowerCase() === 'people' || state.taxonomyNodes.some(n => n.parent_id === r.id)));
    const keywordRoots = allRoots.filter(r => r.has_face === 0);
    
    const matches = state.taxonomyNodes.filter(n => n.name.toLowerCase() === inputName.toLowerCase());
    
    if (isPersonField) {
        if (matches.length > 0) {
            const peopleMatches = matches.filter(m => {
                const rootName = rootOf(m.tag);
                const rootNode = allRoots.find(r => r.name.toLowerCase() === rootName.toLowerCase());
                return rootNode && rootNode.has_face === 1;
            });
            
            if (peopleMatches.length === 1) {
                return peopleMatches[0].tag;
            } else if (peopleMatches.length > 1) {
                const options = peopleMatches.map(m => m.tag);
                const res = await askWhereItGoes(
                    "Resolve Ambiguous Person",
                    `Multiple folders exist for "${inputName}". Please select which one you mean:`,
                    options,
                    false
                );
                return res ? res.root : null;
            }
        }
        
        const peopleRootNames = peopleRoots.map(r => r.name);
        if (peopleRootNames.length === 0) {
            if (!isJustLooking()) await api.fetch('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: "People", has_face: 1 })
            });
            await loadTaxonomy();
            return `People/${inputName}`;
        } else if (peopleRootNames.length === 1) {
            const targetPath = `${peopleRootNames[0]}/${inputName}`;
            if (!isJustLooking()) await api.fetch('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: targetPath })
            });
            await loadTaxonomy();
            return targetPath;
        } else {
            const res = await askWhereItGoes(
                "Resolve New Person",
                `The person "${inputName}" is new. Please select which people folder to add them under:`,
                peopleRootNames,
                false
            );
            if (!res) return null;
            const targetPath = `${res.root}/${inputName}`;
            if (!isJustLooking()) await api.fetch('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: targetPath })
            });
            await loadTaxonomy();
            return targetPath;
        }
    } else {
        if (matches.length === 1) {
            return matches[0].tag;
        } else if (matches.length > 1) {
            const options = matches.map(m => m.tag);
            const res = await askWhereItGoes(
                "Resolve Ambiguous Tag",
                `Multiple tag paths exist for "${inputName}". Please select which one you mean:`,
                options,
                false
            );
            return res ? res.root : null;
        }
        
        const rootNames = keywordRoots.map(r => r.name);
        const res = await askWhereItGoes(
            "Resolve New Tag",
            `The tag "${inputName}" is new. Please specify which category it should be placed under, or create a new one:`,
            rootNames,
            true
        );
        if (!res) return null;
        
        let targetPath;
        if (res.action === 'create_root') {
            const rootRes = isJustLooking() ? { success: true } : await api.json('/api/taxonomy/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: res.name, has_face: res.hasFace ? 1 : 0 })
            });
            
            if (!rootRes.success) {
                alert("Error creating root category: " + rootRes.error);
                return null;
            }
            targetPath = `${res.name}/${inputName}`;
        } else {
            targetPath = `${res.root}/${inputName}`;
        }
        
        if (!isJustLooking()) await api.fetch('/api/taxonomy/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: targetPath })
        });
        await loadTaxonomy();
        return targetPath;
    }
}
