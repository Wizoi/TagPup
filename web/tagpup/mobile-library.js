/**
 * The phone's list of libraries: one big button each. The one open goes back to it; another opens the phone's page
 * on that library, a page of its own (goToLibrary), as the desktop page does: nothing of this library is carried
 * over or has to be reset. A page whose URL names no library goes to the one this browser opened last, while the
 * server still lists it (rememberedAndListed), and otherwise shows the list.
 *
 * The list is asked for each time the screen is shown, so a library made on the PC appears without a reload; a reply
 * to an older ask is dropped, so a slow first reply cannot draw over a newer one.
 */
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { goToLibrary, rememberedAndListed } from './common/library.js';
import { state } from './mobile-state.js';

function libraryChoice(name) {
    const current = name === state.library;
    const choice = buildElement('button', {
        className: current ? 'library-choice current' : 'library-choice',
        text: name,
        attrs: { type: 'button', 'aria-current': current ? 'page' : null },
    });
    choice.addEventListener('click', () => (current ? window.history.back() : goToLibrary(name)));
    return choice;
}

/** Ask for the libraries and show them, or say the read failed with Try again. */
export function showLibraries() {
    const asked = ++state.librariesAsked;
    const list = document.getElementById('library-list');
    const problem = document.getElementById('library-problem');
    problem.classList.add('hidden');
    api.json('/api/databases')
        .then((data) => {
            if (!data || !Array.isArray(data.databases)) throw new Error('the server sent no list of libraries');
            return data.databases;
        })
        .then((names) => {
            if (asked !== state.librariesAsked) return;
            const remembered = state.library ? '' : rememberedAndListed(names);
            if (remembered) {
                goToLibrary(remembered);
                return;
            }
            replaceContent(list, ...names.map(libraryChoice));
        }, () => {
            if (asked !== state.librariesAsked) return;
            replaceContent(list);
            problem.classList.remove('hidden');
        });
}

/** Try again asks once more. */
export function wireLibraries() {
    document.getElementById('library-retry').addEventListener('click', showLibraries);
}
