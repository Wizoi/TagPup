/**
 * The phone page's screens: one shown at a time, each a step in the browser's history, so the phone's Back (Android's
 * gesture, the bar's arrow) goes up a screen and leaves the page only from the first one.
 *
 * The screen shown is kept in history.state: a reload (pulling the page down) opens the screen it was on, and the
 * entries before it are still there to go back to. An entry holds a place on its screen too (a level of Browse, the
 * Grid's view), so a drill-down level is a step of its own.
 *
 * A page opened afresh on a library puts the list of libraries under Browse: Back at the top of Browse opens the list,
 * not off the page, and that bottom step (`first`) has no Back of its own.
 *
 * A sheet over a screen (the Photo screen's Edit) is a step of its own too, its entry the screen's with `sheet`: Back
 * closes the sheet before it leaves the screen, and Back or Forward onto that step opens it again (whenSheet). That entry
 * is the one record of whether the sheet is open: the sheet is drawn from it after every step (its push, Back, Forward,
 * a reload), and a tap that must not act under an open sheet asks sheetShown(), never the sheet. A Close still on its
 * way back, a page kept in the back-forward cache, cannot leave the two disagreeing.
 *
 * A sheet may change its screen's place (the Filter sheet, mobile-filter.js, the Grid's view): Back from its step to the
 * step under it leaves the screen where the sheet left it, the place carried down onto that step, so closing the sheet
 * keeps what was done under it and a second Back leaves the screen.
 */
import { state } from './mobile-state.js';

/** What runs each time a screen is shown, by its name. */
const onShow = new Map();
/** What runs each time a history step is shown, told whether it has a sheet over its screen. */
const onSheet = [];

/** Run `fn` each time the screen `name` is shown, by a tap, Back or a reload. */
export function whenShown(name, fn) {
    if (!onShow.has(name)) onShow.set(name, []);
    onShow.get(name).push(fn);
}

/** Run `fn(open)` each time a step is shown by Back, Forward or a reload: `open`, the step has the sheet over it. */
export function whenSheet(fn) {
    onSheet.push(fn);
}

function sheetIn(entry) {
    for (const fn of onSheet) fn(Boolean(entry && entry.sheet));
}

/** Is a sheet open over the screen shown: does the step shown have one. */
export function sheetShown() {
    return Boolean(window.history.state && window.history.state.sheet);
}

/** A sheet opened over the screen shown, as a step (Back closes it first), and drawn; nothing when the step has one. */
export function openSheetStep() {
    if (sheetShown()) return;    // open already, or its Close's Back not landed yet: no second step
    window.history.pushState({ ...window.history.state, sheet: true }, '');
    state.stepSheet = true;
    sheetIn(window.history.state);
}

/** The sheet closed by its own button: its step goes, the sheet with it as the Back lands, so the next Back leaves the screen. */
export function closeSheetStep() {
    window.history.back();
}

function sectionOf(name) {
    return [...document.querySelectorAll('[data-screen]')].find((section) => section.dataset.screen === name) || null;
}

/** The screen a history entry holds, when the page has it; else the first screen. */
function screenIn(entry) {
    const name = entry && typeof entry.screen === 'string' ? entry.screen : '';
    return name && sectionOf(name) ? name : state.home;
}

/** The place a history entry holds on its screen, or null. */
function placeIn(entry) {
    return entry && entry.screen === screenIn(entry) && entry.place !== undefined ? entry.place : null;
}

/** Are these the same place: the same level, the same view (each is built by one function, its keys in one order)? */
export function samePlace(a, b) {
    return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/** Is this history entry the page's first step: no Back above it. */
function isFirst() {
    return Boolean(window.history.state && window.history.state.first);
}

/** Show the screen `name` at `place`, its title in the bar and Back unless it is the first step or the first screen's top. */
export function showScreen(name, place = null) {
    for (const section of document.querySelectorAll('[data-screen]')) {
        section.classList.toggle('hidden', section.dataset.screen !== name);
    }
    state.cameFrom = state.screen;
    state.screen = name;
    state.place = place ?? null;
    const section = sectionOf(name);
    document.getElementById('bar-title').textContent = (section && section.dataset.title) || state.library || 'TagPup';
    document.getElementById('bar-back').classList.toggle('hidden', isFirst() || (name === state.home && state.place === null));
    for (const fn of onShow.get(name) || []) fn();
}

/** Open the screen `name` at `place` as a new step in history; nothing when it is already shown there (a second tap). */
export function openScreen(name, place = null) {
    if (state.screen === name && samePlace(state.place, place)) return;
    window.history.pushState({ screen: name, place: place ?? null }, '');
    state.stepSheet = false;
    showScreen(name, place);
}

/** Show the screen shown at another place, in its own step: a choice made on it (the Grid's order), not a step to go Back from. */
export function replacePlace(place) {
    if (samePlace(state.place, place)) return;
    window.history.replaceState({ ...window.history.state, place: place ?? null }, '');
    showScreen(state.screen, place);
}

/** Up a step: Back, or from the page's first step (the list under Browse), Browse opened again. */
export function upAScreen() {
    if (isFirst()) openScreen(state.home);
    else window.history.back();
}

/** Back goes up a screen: the browser's own Back and the bar's. */
export function wireScreens() {
    window.addEventListener('popstate', (event) => {
        let entry = event.state;
        // Back from a sheet's step to the one under it, on the same screen: the place the sheet changed is carried down.
        if (state.stepSheet && entry && !entry.sheet && screenIn(entry) === state.screen && !samePlace(placeIn(entry), state.place)) {
            entry = { ...entry, place: state.place };
            window.history.replaceState(entry, '');
        }
        state.stepSheet = Boolean(entry && entry.sheet);
        const name = screenIn(entry);
        const place = placeIn(entry);
        // The same screen at the same place is only ever a sheet's step and the one under it: the screen stays.
        if (name !== state.screen || !samePlace(place, state.place)) showScreen(name, place);
        sheetIn(entry);
    });
    document.getElementById('bar-back').addEventListener('click', upAScreen);
}

/**
 * Show the first screen, or the one a reload left: a page on a library opens on browsing it (the list of libraries the
 * step under it), or on the photos of the view its address names (`state.linked`), a step above Browse; one on none
 * opens on the list of libraries.
 */
export function startScreens() {
    state.home = state.library ? 'browse' : 'library';
    const entry = window.history.state;
    if (!entry && state.library) {
        window.history.replaceState({ screen: 'library', place: null, first: true }, '');
        window.history.pushState({ screen: 'browse', place: null }, '');
        if (state.linked) window.history.pushState({ screen: 'grid', place: state.linked }, '');
        showScreen(state.linked ? 'grid' : 'browse', state.linked);
        return;
    }
    const kept = state.library ? screenIn(entry) : state.home;
    if (!entry || entry.screen !== kept) window.history.replaceState({ screen: kept, place: null }, '');
    state.stepSheet = sheetShown();
    showScreen(kept, placeIn(window.history.state));
    sheetIn(window.history.state);
}
