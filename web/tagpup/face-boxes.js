// TagPup's page: the faces on the open photo (docs/ARCHITECTURE.md, "Faces on the photo"; #791).
//
// A small face icon on the photo, when faces were found in it, shows a box over each; a click on a
// box opens a panel to name that face. The faces, their boxes and the pixel size the boxes are in come
// from /api/photo-faces (the strip below the photo reads the same answer); the people a face is like,
// with how much, from /api/face-matches -- TagTuner's own, over the one cache of named faces; and the
// writes are TagTuner's own views of one face (/api/face/match, /api/face/unmatch, /api/faces/exclude).
//
// Choosing a person NAMES THE FACE AND PUTS THE PERSON ON THE PHOTO in one action, so that faces and
// tags cannot drift apart: the tag goes first, through the page's own save of the photo's keywords
// (applySuggestedTagDirect -- its queue, its undo, its placement question for a name the tree does not
// hold), and the face second. If the tag is not added (a failed save, a placement question answered
// "no"), the face is not named. If the face cannot be named afterwards, the panel says so and keeps the
// choice: the tag is already there, and the same click names the face.
//
// Taking a name off a face leaves the person's tag alone: "this face is not them" is not "they are not in
// the photo", and the person's pill is the way to take the tag off. Excluding a face ("not important")
// hides its box; Restore is TagTuner's.
//
// The boxes are in the stored pixels of the file and are placed by the one geometry both pages share
// (boxInContainedImage). A photo stored turned (EXIF Orientation 2 to 8) is shown turned, and its boxes
// are not: they are drawn where the stored pixels put them, which is not where the faces are, until the
// orientation redesign (the panel says so). Never in a folder the library does not hold ("Just look"): no
// faces are recorded there, and the icon is not offered.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { boxInContainedImage } from './common/image-zoom.js';
import { samePath } from './common/paths.js';
import { attachPersonFaces, forgetPersonFaces, hidePersonFaces } from './common/person-faces.js';
import { leafOf, nameProblem, photoAlreadyHas, samePerson } from './common/vocabulary.js';
import { upper } from './hooks.js';
import { state } from './state.js';
import { faceLayer, imageViewer, mainImage } from './elements.js';
import { isJustLooking } from './looking.js';
import { setStatus } from './status.js';
import { namesAPerson } from './tags.js';

/** How many suggestions the panel lists, and the room it keeps from the window's edge. */
const FACE_SUGGESTIONS = 5;
const FACE_PANEL_MARGIN = 8;

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** The faces whose boxes are drawn: not the ones ruled out. */
function drawnFaces() {
    return state.faceBoxes.faces.filter(face => !face.excluded);
}

function faceById(faceId) {
    return state.faceBoxes.faces.find(face => face.id === faceId) || null;
}

function toggleButton() {
    return faceLayer ? faceLayer.querySelector('.face-boxes-toggle') : null;
}

function boxButton(faceId) {
    return faceLayer ? faceLayer.querySelector(`.face-box[data-face-id="${faceId}"]`) : null;
}

function panelElement() {
    return faceLayer ? faceLayer.querySelector('.face-panel') : null;
}

// ---- Which photo, and what it has -------------------------------------------------------

/**
 * The faces of `photoPath` are known (the answer of /api/photo-faces): offer the icon, if there
 * are faces to box and the folder is the library's. A reply for a photo no longer open is dropped by
 * the caller (the strip's own token); a photo shown again keeps the toggle, and the panel if its face
 * is still there.
 */
export function showPhotoFaces(photoPath, data) {
    if (!faceLayer) return;
    const box = state.faceBoxes;
    const faces = (data && Array.isArray(data.faces)) ? data.faces : [];
    const samePhoto = box.path !== null && samePath(box.path, photoPath);
    box.path = photoPath;
    box.faces = faces;
    box.size = (data && Array.isArray(data.size) && data.size.length === 2) ? data.size : null;
    box.turned = Boolean(data && data.turned);
    if (!samePhoto) {
        box.matches = {};
        box.open = null;
    } else if (box.open !== null && !faceById(box.open)) {
        box.open = null;
    }
    redrawFaceBoxes();
}

/** The photo is gone, or has no faces, or another is opening: nothing to box. */
export function clearPhotoFaces() {
    if (!faceLayer) return;
    const box = state.faceBoxes;
    box.path = null;
    box.faces = [];
    box.size = null;
    box.turned = false;
    box.open = null;
    box.matches = {};
    box.busy = false;
    hidePersonFaces();
    faceLayer.classList.add('hidden');
    replaceContent(faceLayer);
}

// ---- Drawing -------------------------------------------------------------------------------

/** Put the layer over the picture, exactly, and draw the icon, the boxes and the open panel. */
export function redrawFaceBoxes() {
    if (!faceLayer || !imageViewer) return;
    const box = state.faceBoxes;
    const faces = drawnFaces();
    const offered = faces.length > 0 && !isJustLooking() && !mainImage.classList.contains('hidden')
        && Boolean(mainImage.getAttribute('src'));
    if (!offered) {
        hidePersonFaces();
        faceLayer.classList.add('hidden');
        replaceContent(faceLayer);
        return;
    }
    faceLayer.classList.remove('hidden');
    faceLayer.style.left = `${mainImage.offsetLeft}px`;
    faceLayer.style.top = `${mainImage.offsetTop}px`;
    faceLayer.style.width = `${mainImage.clientWidth}px`;
    faceLayer.style.height = `${mainImage.clientHeight}px`;

    const focused = faceLayer.contains(document.activeElement) ? document.activeElement : null;
    const focusedFace = focused && focused.dataset ? focused.dataset.faceId : null;
    const hadFocus = focused && focused.classList.contains('face-boxes-toggle');
    const typing = focused && focused.classList.contains('face-panel-input');

    const children = [faceToggle(faces)];
    if (box.shown) {
        const area = { width: mainImage.clientWidth, height: mainImage.clientHeight };
        const natural = box.size ? { width: box.size[0], height: box.size[1] } : null;
        for (const face of faces) {
            const placed = natural ? boxInContainedImage(natural, area, face.box) : null;
            if (placed) children.push(faceBox(face, placed, faces));
        }
        if (box.open !== null && faceById(box.open) && !faceById(box.open).excluded) children.push(facePanel(faceById(box.open)));
    }
    replaceContent(faceLayer, ...children);
    if (box.open !== null && box.shown) placeFacePanel();

    // Drawn again, not gone: the focus stays where it was.
    if (hadFocus) toggleButton().focus({ preventScroll: true });
    else if (focusedFace && boxButton(focusedFace)) boxButton(focusedFace).focus({ preventScroll: true });
    else if (typing && faceLayer.querySelector('.face-panel-input')) {
        const again = faceLayer.querySelector('.face-panel-input');
        again.focus({ preventScroll: true });
        again.setSelectionRange(again.value.length, again.value.length);
    }
}

function faceToggle(faces) {
    const box = state.faceBoxes;
    const unnamed = faces.filter(face => !face.name).length;
    const count = faces.length;
    const button = buildElement('button', {
        className: 'face-boxes-toggle', text: `👤 ${count}`,
        attrs: {
            type: 'button', 'aria-pressed': box.shown ? 'true' : 'false',
            'aria-label': `${count} face${count === 1 ? '' : 's'} found${unnamed ? `, ${unnamed} not named` : ''}. `
                + (box.shown ? 'Hide the boxes' : 'Show a box on each'),
        },
        title: box.shown ? 'Hide the boxes on the faces'
            : `Show the ${count} face${count === 1 ? '' : 's'} found in this photo${unnamed ? ` (${unnamed} not named)` : ''}`,
    });
    button.classList.toggle('is-on', box.shown);
    button.addEventListener('click', () => {
        box.shown = !box.shown;
        if (!box.shown) closeFacePanel({ focus: false });
        redrawFaceBoxes();
        const again = toggleButton();
        if (again) again.focus({ preventScroll: true });
    });
    return button;
}

function faceBox(face, placed, faces) {
    const place = faces.indexOf(face) + 1;
    const who = face.name ? face.name : 'not named';
    const button = buildElement('button', {
        className: 'face-box' + (face.name ? ' is-named' : ' is-unnamed'),
        data: { faceId: face.id },
        attrs: {
            type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': state.faceBoxes.open === face.id ? 'true' : 'false',
            'aria-label': `Face ${place} of ${faces.length}: ${who}`,
        },
        title: face.name ? `${face.name}. Click to change it.` : 'Not named. Click to name it.',
    });
    if (face.id === state.faceBoxes.open) button.classList.add('is-open');
    button.style.left = `${placed.left}px`;
    button.style.top = `${placed.top}px`;
    button.style.width = `${placed.width}px`;
    button.style.height = `${placed.height}px`;
    if (face.name) button.append(buildElement('span', { className: 'face-box-name', text: face.name }));
    button.addEventListener('click', (event) => {
        event.stopPropagation();
        openFacePanel(face.id, { focus: true });
    });
    return button;
}

// ---- The panel of one face -----------------------------------------------------------------

/** Open the panel of a face (closing another's). `focus`: move the focus into it, for a keyboard. */
export function openFacePanel(faceId, { focus = false } = {}) {
    const box = state.faceBoxes;
    if (!faceById(faceId)) return;
    box.open = faceId;
    box.note = '';
    if (box.typedFor !== faceId) box.typed = '';
    redrawFaceBoxes();
    askSuggestions(faceId);
    if (focus) {
        const input = faceLayer.querySelector('.face-panel-input');
        if (input) input.focus({ preventScroll: true });
    }
}

/** Close the panel; the focus goes back to the box it was opened from, unless `focus` is false. */
export function closeFacePanel({ focus = true } = {}) {
    const box = state.faceBoxes;
    const was = box.open;
    if (was === null) return;
    box.open = null;
    box.note = '';
    hidePersonFaces();
    redrawFaceBoxes();
    if (focus && was !== null && boxButton(was)) boxButton(was).focus({ preventScroll: true });
}

function facePanel(face) {
    const box = state.faceBoxes;
    const faces = drawnFaces();
    const place = faces.indexOf(face) + 1;
    const panel = buildElement('div', {
        className: 'face-panel',
        attrs: { role: 'dialog', 'aria-label': `Face ${place} of ${faces.length}`, 'data-own-keys': '' },
    });

    const heading = buildElement('div', { className: 'face-panel-head' }, [
        buildElement('img', { className: 'face-panel-crop', attrs: { src: api.image(`/api/face-crop?id=${face.id}`), alt: '' } }),
        buildElement('div', { className: 'face-panel-title' }, [
            buildElement('div', { className: 'face-panel-who', text: face.name || 'Not named yet' }),
            buildElement('div', { className: 'face-panel-sub', text: `Face ${place} of ${faces.length}` }),
        ]),
    ]);
    const close = buildElement('button', {
        className: 'face-panel-close', text: '✕', title: 'Close (Escape)', attrs: { type: 'button', 'aria-label': 'Close' },
    });
    close.addEventListener('click', () => closeFacePanel());
    heading.append(close);
    panel.append(heading);

    if (box.turned) {
        panel.append(buildElement('p', {
            className: 'face-panel-note', attrs: { role: 'note' },
            text: 'This photo is stored turned, and the box is placed by its stored pixels: it may not sit on the face.',
        }));
    }

    // Who it looks like.
    panel.append(buildElement('div', { className: 'face-panel-section', text: 'Looks like' }));
    panel.append(buildElement('div', { className: 'face-panel-suggestions', attrs: { 'aria-live': 'polite' } }, suggestionButtons(face)));

    // Or a name.
    const input = buildElement('input', {
        className: 'face-panel-input',
        attrs: { type: 'text', list: 'people-datalist', placeholder: 'Type a name...', autocomplete: 'off', 'aria-label': 'Name this face' },
    });
    const apply = buildElement('button', {
        className: 'btn btn-primary btn-sm face-panel-name', text: 'Name', attrs: { type: 'button' },
        title: 'Name this face, and add the person to the photo',
    });
    // What was typed survives the panel being drawn again (the window resized under it).
    input.value = box.typedFor === face.id ? box.typed : '';
    input.addEventListener('input', () => {
        box.typed = input.value;
        box.typedFor = face.id;
    });
    const choose = () => {
        const typed = input.value.trim();
        if (typed) nameFaceAs(face, typed);
    };
    apply.addEventListener('click', choose);
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            choose();
        }
    });
    panel.append(buildElement('div', { className: 'face-panel-section', text: 'Or name it' }));
    panel.append(buildElement('div', { className: 'face-panel-row' }, [input, apply]));

    const note = buildElement('div', {
        className: 'face-panel-message', text: box.note || '', attrs: { role: 'status', 'aria-live': 'polite' },
    });
    panel.append(note);

    // And the ways out of naming it.
    const foot = buildElement('div', { className: 'face-panel-foot' });
    if (face.name) {
        const off = buildElement('button', {
            className: 'btn btn-secondary btn-sm', text: 'Not this person', attrs: { type: 'button' },
            title: 'Take the name off this face. The person stays tagged on the photo.',
        });
        off.addEventListener('click', () => unnameFace(face));
        foot.append(off);
    }
    const exclude = buildElement('button', {
        className: 'btn btn-secondary btn-sm', text: 'Not important', attrs: { type: 'button' },
        title: 'Leave this face out of naming: a passer-by, or not a face. TagTuner can bring it back.',
    });
    exclude.addEventListener('click', () => excludeFace(face));
    foot.append(exclude);
    panel.append(foot);

    panel.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            closeFacePanel();
        }
    });
    return panel;
}

/** Where the panel sits: beside its box, below it, or above where the window ends; never off the window. */
function placeFacePanel() {
    const panel = panelElement();
    const open = state.faceBoxes.open;
    if (!panel || open === null) return;
    const anchor = boxButton(open);
    if (!anchor) return;
    const near = anchor.getBoundingClientRect();
    const size = panel.getBoundingClientRect();
    const room = { width: window.innerWidth, height: window.innerHeight };
    let top = near.bottom + 6;
    if (top + size.height > room.height - FACE_PANEL_MARGIN) top = near.top - 6 - size.height;
    top = Math.max(FACE_PANEL_MARGIN, Math.min(top, room.height - size.height - FACE_PANEL_MARGIN));
    const left = Math.max(FACE_PANEL_MARGIN, Math.min(near.left, room.width - size.width - FACE_PANEL_MARGIN));
    panel.style.top = `${top}px`;
    panel.style.left = `${left}px`;
}

// ---- Who it looks like ---------------------------------------------------------------------

/** The people the photo's other faces already carry: not offered for this one (one person, one face). */
function namedElsewhere(face) {
    return state.faceBoxes.faces.filter(other => other.id !== face.id && other.name && !other.excluded).map(other => other.name);
}

function suggestionButtons(face) {
    const found = state.faceBoxes.matches[face.id];
    if (found === undefined || found === 'loading') {
        return [buildElement('span', { className: 'face-panel-muted', text: 'Looking...' })];
    }
    if (found === 'failed') {
        return [buildElement('span', { className: 'face-panel-muted', text: 'The suggestions could not be loaded.' })];
    }
    const elsewhere = namedElsewhere(face);
    const offered = found.filter(match => !elsewhere.some(name => samePerson(name, match.name))
        && !(face.name && samePerson(face.name, match.name))).slice(0, FACE_SUGGESTIONS);
    if (!offered.length) return [buildElement('span', { className: 'face-panel-muted', text: 'No one it looks like yet.' })];
    return offered.map((match) => {
        const percent = Math.round((match.similarity || 0) * 100);
        const button = buildElement('button', {
            className: 'face-panel-suggestion' + (match.band === 'likely' ? ' is-likely' : ' is-possible'),
            attrs: { type: 'button' },
            title: match.band === 'likely'
                ? `Looks like ${match.name}. Click to name this face and add them to the photo.`
                : `Possibly ${match.name}: a weaker match, so look first. Click to name this face and add them to the photo.`,
        }, [
            buildElement('span', { className: 'face-panel-suggestion-name', text: match.name }),
            buildElement('span', { className: 'face-panel-suggestion-percent', text: `${percent}%` }),
        ]);
        attachPersonFaces(button, match.name);
        button.addEventListener('click', () => nameFaceAs(face, match.name));
        return button;
    });
}

/** Ask who a face looks like, once for the face while its panel is open. */
function askSuggestions(faceId) {
    const box = state.faceBoxes;
    if (box.matches[faceId] !== undefined && box.matches[faceId] !== 'failed') return;
    box.matches[faceId] = 'loading';
    const photoPath = box.path;
    api.fetch(`/api/face-matches?id=${encodeURIComponent(faceId)}`)
        .then(res => {
            if (!res.ok) throw new Error('face matches');
            return res.json();
        })
        .then(found => {
            if (!box.path || !samePath(box.path, photoPath)) return;   // another photo has opened since
            box.matches[faceId] = Array.isArray(found) ? found : [];
        })
        .catch(() => {
            if (box.path && samePath(box.path, photoPath)) box.matches[faceId] = 'failed';
        })
        .finally(() => {
            if (box.path && samePath(box.path, photoPath) && box.open === faceId) {
                const where = faceLayer.querySelector('.face-panel-suggestions');
                if (where) {
                    replaceContent(where, ...suggestionButtons(faceById(faceId)));
                    placeFacePanel();
                }
            }
        });
}

// ---- Naming, taking a name off, ruling out ---------------------------------------------------

function say(text) {
    state.faceBoxes.note = text;
    const where = faceLayer ? faceLayer.querySelector('.face-panel-message') : null;
    if (where) where.textContent = text;
}

function photoHasPerson(photo, name) {
    return (photo.people || []).some(person => samePerson(person, name)) || photoAlreadyHas(photo, name, namesAPerson);
}

/** Say what a refused write said, in the server's own words. */
async function whyRefused(res) {
    try {
        const body = await res.json();
        return body.error || body.message || `The server answered ${res.status}.`;
    } catch (error) {
        return `The server answered ${res.status}.`;
    }
}

async function post(route, body) {
    return api.fetch(route, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) });
}

/**
 * Name `face` as `name` AND put the person on the photo: the tag first, then the face. A typed name the
 * tree does not hold goes through the page's placement question (resolveTagOrPerson), which can be
 * answered "no": then nothing is done. One at a time: a second choice while this one is under way waits
 * for the first to be seen.
 */
export async function nameFaceAs(face, rawName) {
    const box = state.faceBoxes;
    if (box.busy) return false;
    const name = leafOf(rawName);
    const problem = nameProblem(name);
    if (problem) {
        say(problem);
        return false;
    }
    const path = box.path;
    const photo = state.folderPhotos.find(candidate => samePath(candidate.path, path));
    if (!photo) {
        say('This photo is not in the list any more.');
        return false;
    }
    box.busy = true;
    faceLayer.classList.add('is-busy');
    setStatus('busy', `Naming ${name}...`);
    try {
        // The tag. Already on the photo: nothing to write.
        if (!photoHasPerson(photo, rawName)) {
            await upper.applySuggestedTagDirect(rawName, true, path);
            if (!photoHasPerson(photo, rawName)) {
                say(`${name} was not added to the photo, so the face is not named.`);
                setStatus('ready', 'Ready');
                return false;
            }
        }
        // The face. The person is its photo's now; whichever photo is open, this face is named.
        let res;
        try {
            res = await post('/api/face/match', { face_id: face.id, person_name: name });
        } catch (error) {
            say(`${name} is on the photo, but the face could not be named: ${error.message}. Choose again to retry.`);
            setStatus('error', 'The face was not named');
            return false;
        }
        if (!res.ok) {
            say(`${name} is on the photo, but the face was not named: ${await whyRefused(res)}`);
            setStatus('error', 'The face was not named');
            return false;
        }
        forgetPersonFaces();
        setStatus('ready', `Named ${name} and added to the photo`);
        if (box.path && samePath(box.path, path)) {
            const mine = faceById(face.id);
            if (mine) mine.name = name;
            box.matches = {};
            const opener = box.open === face.id;
            box.open = null;
            redrawFaceBoxes();
            if (opener && boxButton(face.id)) boxButton(face.id).focus({ preventScroll: true });
            upper.renderPhotoFaces(path);      // the strip under the photo says the same
        }
        return true;
    } finally {
        box.busy = false;
        if (faceLayer) faceLayer.classList.remove('is-busy');
    }
}

/** Take the name off a face. The person's tag stays on the photo (see the top of this file). */
async function unnameFace(face) {
    const box = state.faceBoxes;
    if (box.busy) return;
    const path = box.path;
    box.busy = true;
    try {
        const res = await post('/api/face/unmatch', { face_id: face.id });
        if (!res.ok) {
            say(`The name was not taken off: ${await whyRefused(res)}`);
            return;
        }
        forgetPersonFaces();
        setStatus('ready', 'Name taken off the face');
        if (box.path && samePath(box.path, path)) {
            const mine = faceById(face.id);
            if (mine) mine.name = null;
            box.matches = {};
            redrawFaceBoxes();
            upper.renderPhotoFaces(path);
        }
    } catch (error) {
        say(`The name was not taken off: ${error.message}`);
    } finally {
        box.busy = false;
    }
}

/** Rule a face out of naming ("not important"): its box goes. */
async function excludeFace(face) {
    const box = state.faceBoxes;
    if (box.busy) return;
    const path = box.path;
    box.busy = true;
    try {
        const res = await post('/api/faces/exclude', { face_ids: [face.id] });
        if (!res.ok) {
            say(`The face was not left out: ${await whyRefused(res)}`);
            return;
        }
        forgetPersonFaces();
        setStatus('ready', 'Face left out');
        if (box.path && samePath(box.path, path)) {
            const mine = faceById(face.id);
            if (mine) {
                mine.excluded = true;
                mine.name = null;
            }
            box.open = null;
            box.matches = {};
            redrawFaceBoxes();
            upper.renderPhotoFaces(path);
        }
    } catch (error) {
        say(`The face was not left out: ${error.message}`);
    } finally {
        box.busy = false;
    }
}

// ---- Wiring ----------------------------------------------------------------------------------

/** Its listeners, which main.js adds once the page has loaded. */
export function wireFaceBoxes() {
    if (!faceLayer) return;
    // The picture changes size with the window, the sidebar and each photo.
    mainImage.addEventListener('load', redrawFaceBoxes);
    window.addEventListener('resize', redrawFaceBoxes);
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(redrawFaceBoxes).observe(mainImage);
    // The panel keeps to its box when the page scrolls under it.
    document.addEventListener('scroll', placeFacePanel, true);
    // Escape closes the panel, wherever the focus is, unless something above it took the key.
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || event.defaultPrevented || state.faceBoxes.open === null) return;
        closeFacePanel();
    });
    // A press outside the panel and the boxes puts it away.
    document.addEventListener('pointerdown', (event) => {
        if (state.faceBoxes.open === null) return;
        const target = event.target;
        if (target && typeof target.closest === 'function' && (target.closest('.face-panel') || target.closest('.face-box'))) return;
        if (target && typeof target.closest === 'function' && target.closest('.person-faces')) return;
        closeFacePanel({ focus: false });
    });
}
