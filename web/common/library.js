/**
 * The library picker both pages show, and the library this browser remembers.
 *
 * Both pages had this word for word, but for what choosing another library does:
 * TagPup asks about unsaved edits first, and puts the list back if you stay. That
 * is `beforeLeaving(go, stay)`, which TagTuner, having no edits to lose, leaves as
 * going at once. TagPup's Change button, which closes the open folder, is TagPup's.
 */
import { api, libraryIn } from './api.js';
import { ruleProblem } from './validate.js';

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

export function goToLibrary(name) {
    window.location.href = '/' + name + '/' + window.location.search;
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
    
    const activeDb = libraryIn(window.location.pathname);

    if (activeDb) rememberLibrary(activeDb);

    api.json('/api/databases')
        .then(data => {
            select.innerHTML = '';
            if (!activeDb) {
                // Nothing in the URL: the library this browser opened last, if it
                // is still there; otherwise the picker, empty, asking for one.
                const remembered = rememberedLibrary();
                if (remembered && data.databases.includes(remembered)) {
                    goToLibrary(remembered);
                    return;
                }
                rememberLibrary(null);
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
        })
        .catch(err => console.error('Error fetching databases:', err));

    select.addEventListener('change', () => {
        const chosen = select.value;
        beforeLeaving(() => goToLibrary(chosen), () => { select.value = activeDb; });
    });

    if (createButton) {
        createButton.addEventListener('click', () => {
            const dbName = prompt('Enter a name for the new database (alphanumeric characters, e.g. "vacation_2026"):');
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
                    window.location.href = '/' + cleanName + '/';
                } else {
                    alert('Error creating database: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(err => alert('Error creating database: ' + err));
        });
    }
}
