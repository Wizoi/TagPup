/**
 * The library picker both pages show, and the library this browser remembers.
 *
 * Both pages had this word for word, but for what choosing another library does:
 * TagPup asks about unsaved edits first, and puts the list back if you stay. That
 * is `beforeLeaving(go, stay)`, which TagTuner, having no edits to lose, leaves as
 * going at once. TagPup's Change button, which closes the open folder, is TagPup's.
 */
import { api, libraryIn, pageFileIn } from './api.js';
import { ruleProblem } from './validate.js';
import { enhanceSelect } from './library-picker.js';

// The library opened last, kept in this browser. The server used to keep it, in
// config.ini, and wrote that file whenever a library was chosen (docs/findings.md,
// #100); now a library is only ever reached by its URL, and this is where a bare
// URL learns which one.
const LIBRARY_KEY = 'tagpup.library';

export function rememberedLibrary() {
    try { return localStorage.getItem(LIBRARY_KEY); } catch (e) { return null; }
}

export function rememberLibrary(name) {
    try {
        if (name) localStorage.setItem(LIBRARY_KEY, name);
        else localStorage.removeItem(LIBRARY_KEY);
    } catch (e) { /* a browser that keeps nothing: the picker asks each time */ }
}

// The library this browser just made, kept for the one page load that follows: that load opens Manage folders.
const WELCOME_KEY = 'tagpup.new-library';

/** What Manage folders says when it opens on a library just made. */
export const NEW_LIBRARY_LINE = 'Add the folders this library holds.';

/**
 * True once, on the first load of a library made just now (and only if this page is that library); the mark is
 * taken whatever the answer, so a later reload, or another library, does not open Manage folders.
 */
export function takeNewLibraryWelcome() {
    let made = null;
    try {
        made = sessionStorage.getItem(WELCOME_KEY);
        sessionStorage.removeItem(WELCOME_KEY);
    } catch (e) { return false; }
    return !!made && made === libraryIn(window.location.pathname);
}

/**
 * The parameters of an address that name a view of a library (web/tagpup/library-source.js): its source, its order and what it
 * shows (`media`). The one list: leaving a view, and going to another library, take all of them away (findings #697).
 */
export const VIEW_PARAMS = Object.freeze(['view', 'value', 'recursive', 'order', 'media']);

/**
 * The same page's address for another library: the desktop's or the phone's (/<name>/mobile.html), with its folder
 * (?path); a view of this library does not go with it.
 */
export function libraryAddress(name) {
    const params = new URLSearchParams(window.location.search);
    for (const own of VIEW_PARAMS) params.delete(own);
    const search = params.toString();
    return '/' + name + '/' + pageFileIn(window.location.pathname) + (search ? '?' + search : '');
}

/** Go to the same page on another library: another library is another page, never this one reset. */
export function goToLibrary(name) {
    window.location.href = libraryAddress(name);
}

/** The library this page's URL names, or '' for none; one named is remembered as the one opened last. */
export function libraryOfThePage() {
    const name = libraryIn(window.location.pathname);
    if (name) rememberLibrary(name);
    return name;
}

/**
 * Where a page whose URL names no library goes: the library this browser opened last, while `listed` (the names
 * /api/databases gives) still holds it; otherwise '' and it is forgotten, and the page asks for one.
 */
export function rememberedAndListed(listed) {
    const remembered = rememberedLibrary();
    if (remembered && listed.includes(remembered)) return remembered;
    rememberLibrary(null);
    return '';
}

/**
 * Fill `select` with the libraries, and go to the one chosen; `createButton` makes a
 * new one. A page whose URL names no library goes to the one this browser opened
 * last, if it is still there, and otherwise shows the picker asking for one.
 * `beforeLeaving(go, stay)` is asked before going to another library: it calls
 * `go()` to leave, or `stay()` to stay on this one.
 */
export function initDatabaseSelector(select, createButton, { beforeLeaving = (go) => go() } = {}) {
    if (!select) return;
    // The list that opens is the page's own, not the browser's (library-picker.js, #909).
    const picker = enhanceSelect(select);

    const activeDb = libraryOfThePage();

    api.json('/api/databases')
        .then(data => {
            select.innerHTML = '';
            if (!activeDb) {
                // Nothing in the URL: the library this browser opened last, if it
                // is still there; otherwise the picker, empty, asking for one.
                const remembered = rememberedAndListed(data.databases);
                if (remembered) {
                    goToLibrary(remembered);
                    return;
                }
                const ask = document.createElement('option');
                ask.value = '';
                ask.textContent = 'Choose a library\u2026';
                ask.disabled = true;
                ask.selected = true;
                select.appendChild(ask);
            }

            data.databases.forEach(db => {
                const option = document.createElement('option');
                option.value = db;
                option.textContent = db;
                if (db === activeDb) {
                    option.selected = true;
                }
                select.appendChild(option);
            });
            picker.refresh();
        })
        .catch(err => console.error('Error fetching databases:', err));

    select.addEventListener('change', () => {
        const chosen = select.value;
        beforeLeaving(() => goToLibrary(chosen), () => { select.value = activeDb; picker.refresh(); });
    });

    if (createButton) {
        createButton.addEventListener('click', () => {
            const dbName = prompt('Enter a name for the new library (alphanumeric characters, e.g. "vacation_2026"):');
            if (!dbName) return;
            
            let cleanName = dbName.trim();
            if (!cleanName) return;
            if (cleanName.endsWith('.db')) {
                cleanName = cleanName.substring(0, cleanName.length - 3);
            }
            
            // The server's rule, reserved names and all (tagpup/core/validation.py).
            const refused = ruleProblem('library name', cleanName);
            if (refused) {
                alert(refused);
                return;
            }
            
            api.json('/api/databases/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ db_name: cleanName })
            })
            .then(data => {
                if (data.success) {
                    // Only a library made just now opens Manage folders on arrival (takeNewLibraryWelcome).
                    try { sessionStorage.setItem(WELCOME_KEY, cleanName); } catch (e) { /* no welcome, nothing lost */ }
                    window.location.href = '/' + cleanName + '/';
                } else {
                    alert('Error creating library: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(err => alert('Error creating library: ' + err));
        });
    }
}
