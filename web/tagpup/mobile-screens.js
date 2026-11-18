/**
 * The phone page's screens: one shown at a time, each a step in the browser's history, so the phone's Back (Android's
 * gesture, the bar's arrow) goes up a screen and leaves the page only from the first one.
 *
 * The screen shown is kept in history.state: a reload (pulling the page down) opens the screen it was on, and the
 * entries before it are still there to go back to.
 */
import { state } from './mobile-state.js';

/** What runs each time a screen is shown, by its name. */
const onShow = new Map();

/** Run `fn` each time the screen `name` is shown, by a tap, Back or a reload. */
export function whenShown(name, fn) {
    if (!onShow.has(name)) onShow.set(name, []);
    onShow.get(name).push(fn);
}

function sectionOf(name) {
    return [...document.querySelectorAll('[data-screen]')].find((section) => section.dataset.screen === name) || null;
}

/** The screen a history entry holds, when the page has it; else the first screen. */
function screenIn(entry) {
    const name = entry && typeof entry.screen === 'string' ? entry.screen : '';
    return name && sectionOf(name) ? name : state.home;
}

/** Show the screen `name`, its title in the bar and Back unless it is the first screen. */
export function showScreen(name) {
    for (const section of document.querySelectorAll('[data-screen]')) {
        section.classList.toggle('hidden', section.dataset.screen !== name);
    }
    state.screen = name;
    const section = sectionOf(name);
    document.getElementById('bar-title').textContent = (section && section.dataset.title) || state.library || 'TagPup';
    document.getElementById('bar-back').classList.toggle('hidden', name === state.home);
    for (const fn of onShow.get(name) || []) fn();
}

/** Open the screen `name` as a new step in history; nothing when it is already shown. */
export function openScreen(name) {
    if (state.screen === name) return;
    window.history.pushState({ screen: name }, '');
    showScreen(name);
}

/** Back goes up a screen: the browser's own Back and the bar's. */
export function wireScreens() {
    window.addEventListener('popstate', (event) => showScreen(screenIn(event.state)));
    document.getElementById('bar-back').addEventListener('click', () => window.history.back());
}

/**
 * Show the first screen, or the one a reload left: a page on a library opens on browsing it, one on none on the list
 * of libraries.
 */
export function startScreens() {
    state.home = state.library ? 'browse' : 'library';
    const entry = window.history.state;
    const kept = state.library ? screenIn(entry) : state.home;
    if (!entry || entry.screen !== kept) window.history.replaceState({ screen: kept }, '');
    showScreen(kept);
}
