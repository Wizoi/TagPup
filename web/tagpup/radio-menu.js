// TagPup's page: a button in a library view's floating header that opens a small menu of radio items -- Sort by (sort-menu.js,
// #714) and Show (filter-menu.js) -- the one place such a menu is opened, closed and walked. Each menu says what its items mean
// and what a choice does; this is the rest.
//
// The keys: on the button, Enter and Space open and close it (the button's own click), ArrowDown and ArrowUp open it on the item
// the menu names (`prepare`); in it, ArrowDown and ArrowUp move through its items (Home, End), Enter and Space choose, Escape
// closes it and gives the focus back to the button, Tab closes it. A click or the focus anywhere else closes it, and so does the
// view changing under it (`follow`): it is drawn for one view. The page steps through photos on the arrow keys: the button and the
// menu keep them (`data-own-keys`). `held` is the menu's part of the page's state, { open, token }.

/**
 * A radio menu on `button`. `prepare(up)` is asked as it opens (`up`: ArrowUp opened it): { token, target } -- the view it is
 * drawn for and the item to focus -- or null when there is no view to open it for. `choose(item)` is called after the menu has
 * closed onto its button.
 */
export function radioMenu({ button, menu, held, prepare, choose }) {
    const items = () => [...menu.querySelectorAll('[role="menuitemradio"]')];

    function onOutside(event) {
        if (menu.contains(event.target) || button.contains(event.target)) return;
        close();
    }

    /** Close the menu; with `returnFocus` the button has the focus again. */
    function close({ returnFocus = false } = {}) {
        if (!held.open) return;
        held.open = false;
        held.token = 0;
        menu.classList.add('hidden');
        button.setAttribute('aria-expanded', 'false');
        document.removeEventListener('mousedown', onOutside, true);
        document.removeEventListener('focusin', onOutside, true);
        if (returnFocus) button.focus();
    }

    function open(up = false) {
        const at = prepare(up);
        if (!at) return;
        if (!held.open) {
            held.open = true;
            menu.classList.remove('hidden');
            button.setAttribute('aria-expanded', 'true');
            document.addEventListener('mousedown', onOutside, true);
            document.addEventListener('focusin', onOutside, true);
        }
        held.token = at.token;
        (at.target || items()[0]).focus();
    }

    function chosen(item) {
        close({ returnFocus: true });
        choose(item);
    }

    /** The view shown is `token` (0: none): a menu drawn for another closes, its focus back on the button if it had it. */
    function follow(token) {
        if (held.open && token !== held.token) close({ returnFocus: menu.contains(document.activeElement) });
    }

    function wire() {
        button.addEventListener('click', () => {
            if (held.open) close({ returnFocus: true });
            else open(false);
        });
        button.addEventListener('keydown', (event) => {
            if (event.ctrlKey || event.metaKey || event.altKey) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                event.stopPropagation();
                open(event.key === 'ArrowUp');
            } else if (event.key === 'Escape' && held.open) {
                event.preventDefault();
                event.stopPropagation();
                close({ returnFocus: true });
            }
        });
        menu.addEventListener('keydown', (event) => {
            const all = items();
            const at = all.indexOf(document.activeElement);
            const moves = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: all.length - 1 };
            if (event.key in moves) {
                event.preventDefault();
                event.stopPropagation();   // the page steps through photos on the arrow keys
                all[(moves[event.key] + all.length) % all.length].focus();
            } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();    // one column: nowhere to go, and not the page's
                event.stopPropagation();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                close({ returnFocus: true });
            } else if (event.key === 'Tab') {
                close();
            } else if ((event.key === 'Enter' || event.key === ' ') && at >= 0) {
                event.preventDefault();    // chosen here, not by the button's own click as well
                event.stopPropagation();
                chosen(all[at]);
            }
        });
        menu.addEventListener('click', (event) => {
            const item = event.target.closest('[role="menuitemradio"]');
            if (item && menu.contains(item)) chosen(item);
        });
    }

    return { items, close, follow, wire };
}
