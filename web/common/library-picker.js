/**
 * The library picker as a listbox of the page's own (#909).
 *
 * A native select's open list is an operating-system popup: Chrome on Windows drew a blank ~45 px block under the last
 * option (the owner's screenshot), and nothing a page styles reaches a popup's height (#782 fixed only a hairline). So
 * the list is the page's: a button that shows the library, and a `role="listbox"` that opens under it. The native
 * `<select>` stays, hidden, as the one place the choice lives: `initDatabaseSelector` (library.js) fills it, reads its
 * `value` and hears its `change`, and the page may still disable or focus it (folder.js); this widget follows it.
 *
 * Keys: on the button, Down, Up, Enter, Space or a letter open the list; in the list, Down/Up/Home/End move, Enter or
 * Space choose, Escape closes and gives the focus back to the button, Tab closes, and typing a name's first letters
 * goes to the first library starting so. A click outside closes it; a click on an option chooses it.
 */

const SOURCE_CLASS = 'lib-picker-source';
let pickerCount = 0;

/**
 * Put a picker over `select`. Returns `{ refresh }`: call it after the select's options or value changed in code
 * (the widget hears the select's attributes and options itself, but not a `value` assigned).
 */
export function enhanceSelect(select, { label = 'Library' } = {}) {
    const doc = select.ownerDocument;
    const win = doc.defaultView;
    const id = 'lib-picker-' + (++pickerCount);

    const root = doc.createElement('span');
    root.className = 'lib-picker';
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'lib-picker-button';
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', id + '-list');
    const text = doc.createElement('span');
    text.className = 'lib-picker-text';
    const caret = doc.createElement('span');
    caret.className = 'lib-picker-caret';
    caret.setAttribute('aria-hidden', 'true');
    caret.textContent = '▾';
    button.append(text, caret);
    const list = doc.createElement('ul');
    list.className = 'lib-picker-list hidden';
    list.id = id + '-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', label);
    list.tabIndex = -1;
    root.append(button, list);

    select.classList.add(SOURCE_CLASS);
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    select.parentNode.insertBefore(root, select);
    // A label for the select, or the page focusing it, lands on the button.
    // The page enables the select and focuses it in one breath (folder.js); the button follows the select at once.
    select.focus = () => { refresh(); button.focus(); };
    if (select.id) {
        button.id = select.id + '-button';
        for (const l of doc.querySelectorAll('label[for="' + select.id + '"]')) l.htmlFor = button.id;
    }

    let rows = [];       // [{ value, li }] of the choosable options
    let active = -1;     // the row the arrow keys are on
    let typed = '';
    let typedAt = 0;
    let closedAt = 0;    // when a loss of focus last closed the list

    const isOpen = () => !list.classList.contains('hidden');

    function refresh() {
        const chosen = select.selectedOptions && select.selectedOptions[0];
        text.textContent = chosen ? chosen.textContent : '';
        button.setAttribute('aria-label', label + ': ' + text.textContent);
        button.disabled = select.disabled;
        if (button.disabled && isOpen()) close(false);
        list.textContent = '';
        rows = [];
        for (const option of select.options) {
            if (option.disabled) continue;   // the "Choose a library" prompt is shown on the button, not offered
            const li = doc.createElement('li');
            li.className = 'lib-picker-option';
            li.setAttribute('role', 'option');
            li.id = id + '-option-' + rows.length;
            li.textContent = option.textContent;
            li.setAttribute('aria-selected', option === chosen ? 'true' : 'false');
            li.addEventListener('mousedown', (e) => e.preventDefault());   // keep the focus in the list
            const value = option.value;
            li.addEventListener('click', () => choose(value));
            list.appendChild(li);
            rows.push({ value, li });
        }
        if (isOpen() && rows.length) setActive(active);
    }

    function setActive(index) {
        if (!rows.length) return;
        active = Math.max(0, Math.min(rows.length - 1, index));
        rows.forEach((row, i) => row.li.classList.toggle('active', i === active));
        list.setAttribute('aria-activedescendant', rows[active].li.id);
        if (rows[active].li.scrollIntoView) rows[active].li.scrollIntoView({ block: 'nearest' });
    }

    function open() {
        if (button.disabled || isOpen()) return;
        refresh();
        if (!rows.length) return;
        list.classList.remove('hidden');
        button.setAttribute('aria-expanded', 'true');
        const current = rows.findIndex((row) => row.value === select.value);
        setActive(current < 0 ? 0 : current);
        list.focus();
        doc.addEventListener('mousedown', outside, true);
    }

    function close(giveFocusBack = true) {
        if (!isOpen()) return;
        list.classList.add('hidden');
        button.setAttribute('aria-expanded', 'false');
        list.removeAttribute('aria-activedescendant');
        doc.removeEventListener('mousedown', outside, true);
        if (giveFocusBack) button.focus();
    }

    function closeByFocus() {
        if (isOpen()) closedAt = Date.now();
        close(false);
    }

    function outside(event) {
        if (!root.contains(event.target)) close(false);
    }

    function choose(value) {
        const changed = value !== select.value;
        close();
        if (!changed) return;
        select.value = value;
        refresh();
        select.dispatchEvent(new win.Event('change', { bubbles: true }));
    }

    function typeAhead(char) {
        const now = Date.now();
        typed = (now - typedAt > 700 ? '' : typed) + char.toLowerCase();
        typedAt = now;
        const from = typed.length === 1 ? active + 1 : active;   // one letter repeated walks on; a word stays
        for (let n = 0; n < rows.length; n++) {
            const i = (from + n) % rows.length;
            if (rows[i].li.textContent.toLowerCase().startsWith(typed)) { setActive(i); return true; }
        }
        return false;
    }

    // A button that does not take focus on a press (Safari, Firefox on a Mac) leaves it in the list, which then closes on
    // the press itself; the click that ends the same gesture would open it again.
    button.addEventListener('click', () => {
        if (isOpen()) close();
        else if (Date.now() - closedAt > 300) open();
    });
    button.addEventListener('keydown', (e) => {
        if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
            e.preventDefault();
            open();
        } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault();
            open();
            typeAhead(e.key);
        }
    });
    list.addEventListener('keydown', (e) => {
        const key = e.key;
        if (key === 'ArrowDown') setActive(active + 1);
        else if (key === 'ArrowUp') setActive(active - 1);
        else if (key === 'Home') setActive(0);
        else if (key === 'End') setActive(rows.length - 1);
        else if (key === 'Enter' || key === ' ') { if (rows[active]) choose(rows[active].value); }
        else if (key === 'Escape') { e.stopPropagation(); close(); }
        else if (key === 'Tab') { close(false); return; }
        else if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) typeAhead(key);
        else return;
        e.preventDefault();
    });
    list.addEventListener('focusout', (e) => {
        if (!e.relatedTarget || !root.contains(e.relatedTarget)) closeByFocus();
    });

    const Observer = win.MutationObserver;
    if (Observer) {
        new Observer(refresh).observe(select, { attributes: true, attributeFilter: ['disabled'], childList: true, subtree: true });
    }
    refresh();
    return { refresh, root, button, list };
}
