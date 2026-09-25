/**
 * The gear at the top of each page: a small menu of what is not the page's own work --
 * the tag editor, the library's settings, the other app on the same library
 * (docs/ARCHITECTURE.md, phase 7.6).
 *
 * Each page's index.html holds its gear's button and items, in the page's own style:
 *
 *     <div class="gear">
 *       <button id="btn-gear" aria-haspopup="menu" aria-controls="gear-menu">...</button>
 *       <div id="gear-menu" class="gear-menu hidden" role="menu">
 *         <button role="menuitem" data-action="tag-editor">Tag editor</button>
 *         <a role="menuitem" data-app="tuner" aria-disabled="true">Open in TagTuner</a>
 *       </div>
 *     </div>
 *
 * and this makes it a menu: it opens on click, Enter or Space -- the button's own; ArrowUp,
 * ArrowDown, Home and End move through its items; Escape or Tab closes it, Escape putting
 * the focus back on the gear; a click anywhere else closes it. An item marked
 * aria-disabled is shown and focusable -- its title says why -- but does nothing.
 *
 * The pages step through photos on the arrow keys. While the menu is open it keeps every
 * arrow key, and Home and End, to itself; ArrowLeft and ArrowRight once stepped the photo
 * behind it. A closed gear keeps none of them: the focus comes back to it when the tag
 * editor closes, and an arrow there opened the menu instead of stepping the photos, as
 * every arrow did before there was a gear.
 *
 * An item with data-app is a link to that app's page on this library. Its address comes
 * from the server (/api/apps): the ports are the process's, and a page that spelled
 * them would go on pointing at the usual port when the launcher had been told another.
 */
import { api } from './api.js';

/** Each app's page on this library, from the server once: { tagpup: url, tuner: url }. */
let gearAppUrls = null;

function gearItems(menu) {
    return [...menu.querySelectorAll('[role="menuitem"]')];
}

function isDisabledItem(item) {
    return item.getAttribute('aria-disabled') === 'true';
}

/** Point each data-app link at that app's page for this library, once it is known. */
function fillAppLinks(menu) {
    const links = menu.querySelectorAll('a[data-app]');
    if (!links.length) return Promise.resolve();
    if (!gearAppUrls) {
        gearAppUrls = api.json('/api/apps')
            .then(data => (data && data.apps) || {})
            .catch(err => {
                console.error('Could not ask where the other app is:', err);
                gearAppUrls = null;   // ask again next time
                return {};
            });
    }
    return Promise.resolve(gearAppUrls).then(urls => {
        links.forEach(link => {
            const url = urls[link.dataset.app];
            if (url) {
                link.href = url;
                link.removeAttribute('aria-disabled');
            } else {
                link.removeAttribute('href');
                link.setAttribute('aria-disabled', 'true');
            }
        });
    });
}

/**
 * Make `button` open `menu`. `actions` maps an item's data-action to what it does.
 * Returns { open, close, isOpen, ready }: `ready` settles once the links know where
 * they go.
 */
export function wireGear(button, menu, actions = {}) {
    if (!button || !menu) return null;
    let ready = Promise.resolve();

    const isOpen = () => !menu.classList.contains('hidden');

    const focusItem = (index) => {
        const items = gearItems(menu);
        if (!items.length) return;
        const at = (index + items.length) % items.length;
        items[at].focus();
    };

    const close = ({ returnFocus = true } = {}) => {
        if (!isOpen()) return;
        menu.classList.add('hidden');
        button.setAttribute('aria-expanded', 'false');
        document.removeEventListener('mousedown', onOutside, true);
        if (returnFocus) button.focus();
    };

    const open = (focusAt = 0) => {
        if (!isOpen()) {
            menu.classList.remove('hidden');
            button.setAttribute('aria-expanded', 'true');
            document.addEventListener('mousedown', onOutside, true);
        }
        ready = fillAppLinks(menu);
        focusItem(focusAt);
        return ready;
    };

    function onOutside(e) {
        if (menu.contains(e.target) || button.contains(e.target)) return;
        close({ returnFocus: false });
    }

    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    menu.setAttribute('role', 'menu');
    gearItems(menu).forEach(item => item.setAttribute('tabindex', '-1'));

    button.addEventListener('click', () => {
        if (isOpen()) close();
        else open(0);
    });
    button.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen()) {
            e.preventDefault();
            e.stopPropagation();
            close();
        }
    });

    menu.addEventListener('keydown', (e) => {
        const items = gearItems(menu);
        const at = items.indexOf(document.activeElement);
        const moves = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 };
        if (e.key in moves) {
            e.preventDefault();
            e.stopPropagation();   // the pages step through photos on the arrow keys
            focusItem(moves[e.key]);
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault();    // a menu of one column: nowhere to go, and not the page's
            e.stopPropagation();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            close();
        } else if (e.key === 'Tab') {
            close({ returnFocus: false });
        } else if ((e.key === 'Enter' || e.key === ' ') && at >= 0) {
            // A link follows itself on Enter; Space, and a disabled item, do not.
            if (e.key === ' ' || isDisabledItem(items[at])) e.preventDefault();
            e.stopPropagation();
            if (e.key === ' ' && !isDisabledItem(items[at])) items[at].click();
        }
    });

    menu.addEventListener('click', (e) => {
        const item = e.target.closest('[role="menuitem"]');
        if (!item || !menu.contains(item)) return;
        if (isDisabledItem(item)) {
            e.preventDefault();
            return;
        }
        const action = item.dataset.action;
        // The menu closes first, so what the item opens takes the focus from the gear
        // and gives it back to the gear when it closes.
        close();
        if (action && actions[action]) {
            e.preventDefault();
            actions[action]();
        }
    });

    return { open, close, isOpen, ready: () => ready };
}
