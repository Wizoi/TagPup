/**
 * A control drawn as something other than a button -- a pill, a chip, a card, a list row, a picture -- that the
 * keyboard presses as the mouse clicks it. Tab reaches it (a tabindex of 0, unless it has one of its own: a list's
 * roving one), it is announced as a button unless it has a role of its own, and Enter or Space on it is a click, so
 * its click listener stays the one place that says what it does. The pages' keyboard check (tests/frontend/
 * tab-order.mjs, `mouseOnly`) names a shown element with a click and no tabindex; this is what answers it.
 */
export function pressable(element) {
    if (!element) return element;
    if (!element.hasAttribute('tabindex')) element.tabIndex = 0;
    if (!element.hasAttribute('role')) element.setAttribute('role', 'button');
    element.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target !== element) return;   // a key pressed in something inside it is that thing's
        event.preventDefault();               // Space would scroll the panel
        element.click();
    });
    return element;
}
