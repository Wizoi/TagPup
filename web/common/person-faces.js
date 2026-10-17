/**
 * Who a suggested person is: a small popup of their faces on hover or focus, the one both
 * pages use.
 *
 * A suggestion is a name on a button. Scrolling over a list of them says nothing of who each
 * is, and a name alone is no help for the people one knows by face. attachPersonFaces(button,
 * person) makes the button show up to four square crops of that person -- the faces a person
 * decided first, the one most like them first (/api/people-face-samples) -- beside the name.
 * `person` is the person as the server sends them ({id, name, tag}) and is looked up by their id, so two people
 * called alike show their own faces. A bare name is nobody (the leaf is never identity), so it gets no popup.
 *
 * The popup is a picture, not a control: it takes no clicks (pointer-events: none), never
 * the focus, and is described to assistive technology by aria-describedby while it is shown.
 * It goes the moment the pointer or the focus leaves, on a click (the button's own work
 * starts) and on Escape. A person with no face named says so.
 *
 * One answer for everyone, asked for once per page (the server caches it with the faces'
 * decided stamp) and kept for MAX_AGE_MS, so faces named meanwhile -- by this page or the
 * other app -- show at the next hover after that; a page that names faces calls
 * forgetPersonFaces() to ask at once. A hover that has not rested for DELAY_MS shows nothing, so scrolling a
 * long list over the pointer does not flash a popup for each button it passes; a leave
 * before the answer arrives, and a second hover before the first answer, never show the
 * first's. Names and everything else go in as text.
 */
import { api } from './api.js';
import { buildElement } from './dom.js';
import { personTitle } from './vocabulary.js';

/** How long the pointer rests on a button before the popup shows. */
export const DELAY_MS = 120;

/** How long the faces asked for are kept before the next hover asks again. */
export const MAX_AGE_MS = 15000;

/** The room between the button and the popup, and the popup's margin from the window's edge. */
const POPUP_GAP = 6;
const POPUP_MARGIN = 4;

const POPUP_ID = 'person-faces-popup';

const hovering = {
    popup: null,
    /** Promise of the answer ({`id:<id>`: [face ids]}), and when it was asked for. */
    samples: null,
    askedAt: 0,
    /** The button the popup is for, and what its aria-describedby held before. */
    shownFor: null,
    describedBefore: null,
    timer: null,
    watch: null,
    /** Which hover is current: a late answer for another is dropped. */
    token: 0,
};

/** What a hover is about: {id, name, title}, from a person record. */
const subjectOf = (person) => ({ id: person.id, name: String(person.name ?? ''), title: personTitle(person) });

/** The faces `samples` hold for the subject, by the id of their node. */
const facesOf = (samples, subject) => samples[`id:${subject.id}`] || [];

/** Ask again at the next hover: faces were named, or a person was. */
export function forgetPersonFaces() {
    hovering.samples = null;
}

function loadFaceSamples() {
    if (hovering.samples && Date.now() - hovering.askedAt > MAX_AGE_MS) hovering.samples = null;
    if (!hovering.samples) {
        hovering.askedAt = Date.now();
        hovering.samples = Promise.resolve().then(() => api.json('/api/people-face-samples'))
            .then((answer) => answer || {})
            .catch((error) => {
                hovering.samples = null;           // the next hover tries again
                throw error;
            });
    }
    return hovering.samples;
}

function popupBox() {
    if (!hovering.popup) {
        hovering.popup = buildElement('div', {
            id: POPUP_ID, className: 'person-faces hidden', attrs: { role: 'tooltip' },
        });
        document.body.append(hovering.popup);
    }
    return hovering.popup;
}

function placePopup(button) {
    const popup = hovering.popup;
    const anchor = button.getBoundingClientRect();
    const size = popup.getBoundingClientRect();
    const room = { width: window.innerWidth, height: window.innerHeight };
    let top = anchor.bottom + POPUP_GAP;
    if (top + size.height > room.height - POPUP_MARGIN) top = anchor.top - POPUP_GAP - size.height;   // above, near the bottom edge
    top = Math.max(POPUP_MARGIN, Math.min(top, room.height - size.height - POPUP_MARGIN));
    const left = Math.max(POPUP_MARGIN, Math.min(anchor.left, room.width - size.width - POPUP_MARGIN));
    popup.style.top = `${top}px`;
    popup.style.left = `${left}px`;
}

function popupContent(subject, ids) {
    const heading = buildElement('div', { className: 'person-faces-name', text: subject.name, title: subject.title });
    if (ids === null) {
        return [heading, buildElement('div', { className: 'person-faces-none', text: 'faces could not be loaded' })];
    }
    if (!ids.length) {
        return [heading, buildElement('div', { className: 'person-faces-none', text: 'no face named yet' })];
    }
    const faces = ids.map((id) => buildElement('img', {
        className: 'person-faces-crop', attrs: { src: api.image(`/api/face-crop?id=${id}`), alt: '' },
    }));
    return [heading, buildElement('div', { className: 'person-faces-row' }, faces)];
}

/** Show the popup beside `button`, if this is still the hover it was asked for. `ids` null: no answer. */
function showPopup(button, subject, token, ids) {
    if (token !== hovering.token || !button.isConnected) return;
    const popup = popupBox();
    popup.replaceChildren(...popupContent(subject, ids));
    popup.classList.remove('hidden');
    hovering.shownFor = button;
    hovering.describedBefore = button.getAttribute('aria-describedby');
    button.setAttribute('aria-describedby', POPUP_ID);
    placePopup(button);
    // A list drawn again under the pointer takes its buttons away without a leave.
    hovering.watch = window.setInterval(() => { if (!button.isConnected) hidePopup(); }, 250);
}

/** Take the popup away, and give the button back what it described itself by. */
export function hidePersonFaces() {
    hovering.token += 1;
    hidePopup();
}

function hidePopup() {
    window.clearTimeout(hovering.timer);
    window.clearInterval(hovering.watch);
    hovering.timer = hovering.watch = null;
    if (hovering.popup) hovering.popup.classList.add('hidden');
    const button = hovering.shownFor;
    hovering.shownFor = null;
    if (button) {
        if (hovering.describedBefore === null) button.removeAttribute('aria-describedby');
        else button.setAttribute('aria-describedby', hovering.describedBefore);
    }
}

function beginHover(button, subject) {
    hidePopup();
    const token = ++hovering.token;
    hovering.timer = window.setTimeout(() => {
        hovering.timer = null;
        loadFaceSamples()
            .then((samples) => showPopup(button, subject, token, facesOf(samples, subject)))
            .catch(() => showPopup(button, subject, token, null));
    }, DELAY_MS);
}

/**
 * Make `button` show the person's faces while the pointer rests on it or it has the focus. `person` is a person record
 * ({id, name, tag}), looked up by id; anything without an id (a bare name, a name no tag has) is nobody and gets no popup. The
 * button is left as it is: its click, its focus order and its label are the page's.
 */
export function attachPersonFaces(button, person) {
    if (!button || !person || typeof person !== 'object' || person.id === undefined || person.id === null) return button;
    const subject = subjectOf(person);
    button.addEventListener('mouseenter', () => beginHover(button, subject));
    button.addEventListener('mouseleave', hidePersonFaces);
    button.addEventListener('focus', () => beginHover(button, subject));
    button.addEventListener('blur', hidePersonFaces);
    button.addEventListener('click', hidePersonFaces);
    button.addEventListener('keydown', (event) => { if (event.key === 'Escape') hidePersonFaces(); });
    return button;
}
