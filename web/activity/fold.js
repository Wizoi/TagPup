// The folded sections (Scheduled jobs, Snapshots, Server, Logs): each a <details class="fold">, folded unless the owner
// opened it, the browser remembering which are open (localStorage, where it may: without it each opens folded, and
// opens and folds all the same). A folded section keeps one line in sight (`#<name>-folded`, written by the section's
// own module through foldLine), so folding it hides nothing that needs the owner. A link to a section in the page's
// nav, or a reach for it from another (showRunLogs), unfolds it.

/** Where the browser keeps the names of the sections left open. */
const OPEN_KEY = 'tagpup_activity_open';

function remembered() {
    try {
        const names = JSON.parse(window.localStorage.getItem(OPEN_KEY) || '[]');
        return Array.isArray(names) ? names : [];
    } catch (_) {
        return [];   // storage refused (a private window, a policy) or never written: every section folded
    }
}

function remember() {
    const open = [...document.querySelectorAll('details.fold[open]')].map(each => each.dataset.fold);
    try {
        window.localStorage.setItem(OPEN_KEY, JSON.stringify(open));
    } catch (_) {
        // Not remembered: the page works the same, and opens folded next time.
    }
}

function foldOf(name) {
    return document.querySelector(`details.fold[data-fold="${name}"]`);
}

/** Unfold the section `name` (its id in the page), if it is a folded one. */
export function unfold(name) {
    const fold = foldOf(name);
    if (fold) fold.open = true;   // its toggle event remembers it
}

/** The line a folded section `name` shows: what of it needs the owner without opening it. */
export function foldLine(name, text) {
    document.getElementById(`${name}-folded`).textContent = text;
}

/** Open the sections the browser remembers, remember each opened or folded, and unfold the one a nav link goes to. */
export function wireFolds() {
    const open = new Set(remembered());
    for (const fold of document.querySelectorAll('details.fold')) {
        fold.open = open.has(fold.dataset.fold);
        fold.addEventListener('toggle', remember);
    }
    for (const link of document.querySelectorAll('nav a[href^="#"]')) {
        link.addEventListener('click', () => unfold(link.getAttribute('href').slice(1)));
    }
}
