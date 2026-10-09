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
// Taking a name off a face, or ruling it out ("not important"), takes the person's tag off the photo too,
// unless another face of the photo still carries the person (#861): a face that is not them is not a person
// of the photo, and a tag no face bears out is the drift this module exists to stop. The face goes first and
// the tag second -- the other way round, a failed tag would leave the face named and the photo without the
// person, the very state the owner saw -- through the page's own save of the keywords (the server says which
// tags, `untag`: the one owner of the rule is tagpup.services.face_people). Restore is TagTuner's.
//
// The strip under the photo names a face through the same function as a box does (nameFaceAs), and every
// write here draws the strip and the boxes again from one answer (upper.renderPhotoFaces).
//
// The same boxes, and the same panel, are drawn over the photo in the full-window zoom (web/common/image-zoom.js) while it is
// open (#859): one surface at a time, the zoom's while it covers the window, with the icon to turn the boxes on and off. A panel
// open over the picture goes before the zoom does (Escape, or a click on the backdrop).
//
// The boxes are in the stored pixels of the file and are placed by the one geometry both pages share
// (boxInContainedImage). A photo stored turned (EXIF Orientation 2 to 8) is shown turned, and its boxes
// are not: they are drawn where the stored pixels put them, which is not where the faces are, until the
// orientation redesign (the panel says so). Never in a folder the library does not hold ("Just look"): no
// faces are recorded there, and the icon is not offered.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { boxInContainedImage, onImageZoomChange, onImageZoomDismiss, zoomLayer, zoomedPicture } from './common/image-zoom.js';
import { samePath } from './common/paths.js';
import { attachPersonFaces, forgetPersonFaces, hidePersonFaces } from './common/person-faces.js';
import { choosePerson } from './common/person-choice.js';
import { personLabelNodeOf } from './common/person-label.js';
import {
    GROUP_SEPARATOR, leafOf, nameProblem, personFields, personLabel, personLabelOf, personTitleOf, sameNamed, sameTagPerson,
} from './common/vocabulary.js';
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

/** The layer the boxes are drawn in now: the zoom's while the zoom is open, else the one over the photo in the panel. */
function activeLayer() {
    const zoomed = zoomedPicture();
    return zoomed ? zoomed.layer : faceLayer;
}

function toggleButton() {
    const layer = activeLayer();
    return layer ? layer.querySelector('.face-boxes-toggle') : null;
}

function boxButton(faceId) {
    const layer = activeLayer();
    return layer ? layer.querySelector(`.face-box[data-face-id="${faceId}"]`) : null;
}

function panelElement() {
    const layer = activeLayer();
    return layer ? layer.querySelector('.face-panel') : null;
}

/** Both layers show a naming under way as a busy cursor. */
function showBusy(on) {
    for (const layer of [faceLayer, zoomLayer()]) if (layer) layer.classList.toggle('is-busy', on);
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
    if (zoomLayer()) replaceContent(zoomLayer());
}

// ---- Drawing -------------------------------------------------------------------------------

/**
 * Draw the icon, the boxes and the open panel -- over the photo in the panel, exactly, or over the zoomed picture while the
 * zoom is open (one surface at a time: the other is emptied, so there is never a second copy of a box or a panel).
 */
export function redrawFaceBoxes() {
    if (!faceLayer || !imageViewer) return;
    const box = state.faceBoxes;
    const faces = drawnFaces();
    const zoomed = zoomedPicture();
    const shownInPanel = !mainImage.classList.contains('hidden') && Boolean(mainImage.getAttribute('src'));
    const offered = faces.length > 0 && !isJustLooking() && (zoomed ? true : shownInPanel);
    const layer = zoomed ? zoomed.layer : faceLayer;
    if (zoomed) {
        replaceContent(faceLayer);
        faceLayer.classList.add('hidden');
    } else if (zoomLayer()) {
        replaceContent(zoomLayer());
    }
    if (!offered) {
        hidePersonFaces();
        faceLayer.classList.add('hidden');
        replaceContent(layer);
        return;
    }

    const focused = layer.contains(document.activeElement) ? document.activeElement : null;
    const focusedFace = focused && focused.dataset ? focused.dataset.faceId : null;
    const hadFocus = focused && focused.classList.contains('face-boxes-toggle');
    const typing = focused && focused.classList.contains('face-panel-input');

    // Where the picture is, and the element the icon, the boxes and the panel are drawn in, positioned over it.
    let holder = faceLayer;
    let area;
    if (zoomed) {
        holder = buildElement('div', { className: 'face-zoom-picture' });
        holder.style.left = `${zoomed.left}px`;
        holder.style.top = `${zoomed.top}px`;
        holder.style.width = `${zoomed.width}px`;
        holder.style.height = `${zoomed.height}px`;
        area = { width: zoomed.width, height: zoomed.height };
    } else {
        faceLayer.classList.remove('hidden');
        faceLayer.style.left = `${mainImage.offsetLeft}px`;
        faceLayer.style.top = `${mainImage.offsetTop}px`;
        faceLayer.style.width = `${mainImage.clientWidth}px`;
        faceLayer.style.height = `${mainImage.clientHeight}px`;
        area = { width: mainImage.clientWidth, height: mainImage.clientHeight };
    }

    const children = [faceToggle(faces)];
    if (box.shown) {
        const natural = box.size ? { width: box.size[0], height: box.size[1] } : null;
        for (const face of faces) {
            const placed = natural ? boxInContainedImage(natural, area, face.box) : null;
            if (placed) children.push(faceBox(face, placed, faces));
        }
        if (box.open !== null && faceById(box.open) && !faceById(box.open).excluded) children.push(facePanel(faceById(box.open)));
    }
    if (zoomed) {
        holder.append(...children);
        replaceContent(layer, holder);
    } else {
        replaceContent(faceLayer, ...children);
    }
    if (box.open !== null && box.shown) placeFacePanel();

    // Drawn again, not gone: the focus stays where it was.
    if (hadFocus) toggleButton().focus({ preventScroll: true });
    else if (focusedFace && boxButton(focusedFace)) boxButton(focusedFace).focus({ preventScroll: true });
    else if (typing && layer.querySelector('.face-panel-input')) {
        const again = layer.querySelector('.face-panel-input');
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
    const who = face.name ? personLabelOf(face) : 'not named';
    const button = buildElement('button', {
        className: 'face-box' + (face.name ? ' is-named' : ' is-unnamed'),
        data: { faceId: face.id },
        attrs: {
            type: 'button', 'aria-haspopup': 'dialog', 'aria-expanded': state.faceBoxes.open === face.id ? 'true' : 'false',
            'aria-label': `Face ${place} of ${faces.length}: ${who}`,
        },
        title: face.name ? `${personTitleOf(face)}. Click to change it.` : 'Not named. Click to name it.',
    });
    if (face.id === state.faceBoxes.open) button.classList.add('is-open');
    button.style.left = `${placed.left}px`;
    button.style.top = `${placed.top}px`;
    button.style.width = `${placed.width}px`;
    button.style.height = `${placed.height}px`;
    if (face.name) button.append(buildElement('span', { className: 'face-box-name' }, [personLabelNodeOf(face)]));
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
        const input = panelElement() ? panelElement().querySelector('.face-panel-input') : null;
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
            buildElement('div', { className: 'face-panel-who', text: face.name ? personLabelOf(face) : 'Not named yet', title: face.name ? personTitleOf(face) : '' }),
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
            title: 'Take the name off this face, and the person off the photo unless another face is them.',
        });
        off.addEventListener('click', () => unnameFace(face));
        foot.append(off);
    }
    const exclude = buildElement('button', {
        className: 'btn btn-secondary btn-sm', text: 'Not important', attrs: { type: 'button' },
        title: 'Leave this face out of naming: a passer-by, or not a face. If it was named, the person comes off the photo '
            + 'unless another face is them. TagTuner can bring it back.',
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

/** The photo's other faces that already carry somebody: their people are not offered for this one (one person, one face). */
function namedElsewhere(face) {
    return state.faceBoxes.faces.filter(other => other.id !== face.id && other.name && !other.excluded);
}

/** Who a match names, as a request names them: the person (their id) when the server sent one, else the name. */
function whoOf(row) {
    return row.person && row.person.id !== null && row.person.id !== undefined ? row.person : row.name;
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
    // The same PERSON, by id: two people called Sam are two, and the one a face already is is not offered to it again.
    const offered = found.filter(match => !elsewhere.some(other => sameNamed(other, match))
        && !(face.name && sameNamed(face, match))).slice(0, FACE_SUGGESTIONS);
    if (!offered.length) return [buildElement('span', { className: 'face-panel-muted', text: 'No one it looks like yet.' })];
    return offered.map((match) => {
        const percent = Math.round((match.similarity || 0) * 100);
        const shown = personLabelOf(match);
        const button = buildElement('button', {
            className: 'face-panel-suggestion' + (match.band === 'likely' ? ' is-likely' : ' is-possible'),
            attrs: { type: 'button' },
            title: (match.band === 'likely'
                ? `Looks like ${shown}. Click to name this face and add them to the photo.`
                : `Possibly ${shown}: a weaker match, so look first. Click to name this face and add them to the photo.`)
                + (match.person && match.person.shared ? ` (${personTitleOf(match)})` : ''),
        }, [
            buildElement('span', { className: 'face-panel-suggestion-name' }, [personLabelNodeOf(match)]),
            buildElement('span', { className: 'face-panel-suggestion-percent', text: `${percent}%` }),
        ]);
        attachPersonFaces(button, whoOf(match));
        button.addEventListener('click', () => nameFaceAs(face, whoOf(match)));
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
                const where = panelElement() ? panelElement().querySelector('.face-panel-suggestions') : null;
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
    const where = panelElement() ? panelElement().querySelector('.face-panel-message') : null;
    if (where) where.textContent = text;
    // A face named from the strip has no panel to say it in.
    else setStatus('error', text, { transient: false });
}

/**
 * Is the person among the photo's KEYWORDS? Not photo.people, which also lists a person only a face names (#835). By the id of
 * the person's node: the other Sam's keyword is not this one's.
 */
function photoHasPerson(photo, who) {
    const wanted = who && typeof who === 'object' ? who.tag : who;
    return (photo.tags || []).some(tag => namesAPerson(tag) && sameTagPerson(tag, wanted, state.people));
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
 * Name `face` as a person AND put the person on the photo: the tag first, then the face. `who` is a person (the id of their
 * node travels, and their exact tag is written: the way to name one of two people called alike) or typed text -- a label, a tag
 * path or a name -- looked up among the library's people; a name two people have is asked about, never guessed. A typed name the
 * tree does not hold goes through the page's placement question (resolveTagOrPerson), which can be
 * answered "no": then nothing is done. One at a time: a second choice while this one is under way waits
 * for the first to be seen.
 */
export async function nameFaceAs(face, who) {
    const box = state.faceBoxes;
    if (box.busy) return false;
    let person = who && typeof who === 'object' ? who : null;
    let rawName = person ? person.tag : who;
    if (!person) {
        const found = state.people.match(rawName);
        if (found.kind === 'person') {
            person = found.person;
            rawName = person.tag;
        } else if (found.kind === 'choose') {
            person = await choosePerson(found.people, {
                title: 'Which person?', about: `${found.people.length} people are called ${found.people[0].name}.`,
            });
            if (!person) return false;
            rawName = person.tag;
        } else if (String(rawName).includes(GROUP_SEPARATOR)) {
            say(`"${rawName}" is a person's label, and the page has not read the people yet: try again in a moment.`);
            return false;
        }
    }
    const name = person ? person.name : leafOf(rawName);
    const shown = person ? personLabel(person) : name;
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
    showBusy(true);
    setStatus('busy', `Naming ${shown}...`);
    try {
        // The tag. Already on the photo: nothing to write.
        if (!photoHasPerson(photo, person || rawName)) {
            await upper.applySuggestedTagDirect(rawName, true, path);
            if (!photoHasPerson(photo, person || rawName)) {
                setStatus('ready', 'Ready');
                say(`${shown} was not added to the photo, so the face is not named.`);
                return false;
            }
        }
        // The face. The person is its photo's now; whichever photo is open, this face is named.
        let res;
        try {
            res = await post('/api/face/match', { face_id: face.id, ...personFields(person || name), page_writes_tags: true });
        } catch (error) {
            say(`${shown} is on the photo, but the face could not be named: ${error.message}. Choose again to retry.`);
            setStatus('error', 'The face was not named');
            return false;
        }
        if (!res.ok) {
            say(`${shown} is on the photo, but the face was not named: ${await whyRefused(res)}`);
            setStatus('error', 'The face was not named');
            return false;
        }
        // A face that was another person's: that person's tag goes with the name, unless another face is them.
        const rest = await takeTagsOff(path, await res.json());
        forgetPersonFaces();
        setStatus(rest ? 'ready' : 'error', untagged(rest, `Named ${shown} and added to the photo`), { transient: rest });
        if (box.path && samePath(box.path, path)) {
            const mine = faceById(face.id);
            if (mine) {
                mine.name = name;
                mine.person = person;
            }
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
        showBusy(false);
    }
}

/**
 * The tags the server says to take off `path` (`untag`, by the photo's path as it spells it), off the photo through
 * the page's own save. True when there was nothing to take off or it was taken off.
 */
async function takeTagsOff(path, answer) {
    const entry = Object.entries(answer.untag || {}).find(([where]) => samePath(where, path));
    if (!entry || !entry[1].length) return true;
    return Boolean(await upper.removePhotoTags(path, entry[1]));
}

/** What was done with the person's tag, in a sentence for the status line. */
function untagged(done, what) {
    return done ? what : `${what}, but the person's tag could not be taken off the photo: take it off with its pill`;
}

/** Take the name off a face, and the person's tag off the photo unless another face is them (see the top of this file). */
async function unnameFace(face) {
    const box = state.faceBoxes;
    if (box.busy) return;
    const path = box.path;
    box.busy = true;
    try {
        const res = await post('/api/face/unmatch', { face_id: face.id, page_writes_tags: true });
        if (!res.ok) {
            say(`The name was not taken off: ${await whyRefused(res)}`);
            return;
        }
        const done = await takeTagsOff(path, await res.json());
        forgetPersonFaces();
        setStatus(done ? 'ready' : 'error', untagged(done, 'Name taken off the face'), { transient: done });
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
        const res = await post('/api/faces/exclude', { face_ids: [face.id], page_writes_tags: true });
        if (!res.ok) {
            say(`The face was not left out: ${await whyRefused(res)}`);
            return;
        }
        const done = await takeTagsOff(path, await res.json());
        forgetPersonFaces();
        setStatus(done ? 'ready' : 'error', untagged(done, 'Face left out'), { transient: done });
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
    // The zoom covers the window: the boxes are drawn over its picture while it is open, and over the photo again when it closes.
    onImageZoomChange(redrawFaceBoxes);
    // Escape, or a click on the zoom's backdrop, takes a panel open over the picture away first, and the zoom after.
    onImageZoomDismiss(() => {
        if (state.faceBoxes.open === null || !zoomedPicture()) return false;
        closeFacePanel({ focus: false });
        return true;
    });
    // A press outside the panel and the boxes puts it away.
    document.addEventListener('pointerdown', (event) => {
        if (state.faceBoxes.open === null) return;
        // In the zoom the click that follows is the zoom's: it takes the panel away first (onImageZoomDismiss).
        if (zoomedPicture()) return;
        const target = event.target;
        if (target && typeof target.closest === 'function' && (target.closest('.face-panel') || target.closest('.face-box'))) return;
        if (target && typeof target.closest === 'function' && target.closest('.person-faces')) return;
        closeFacePanel({ focus: false });
    });
}
