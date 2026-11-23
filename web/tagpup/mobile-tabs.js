/**
 * The phone page's bar of tabs (docs/plans/phone-redesign.md, "The three screens"): Library (Browse), Photos (the Grid),
 * Filter and Libraries, at the bottom where a thumb reaches them. A tab is a screen of mobile-screens.js and a press is
 * `openScreen`, a step of history: Back goes to the screen the tab was pressed on, and leaves the page only from the
 * first step, as before. A press on the tab of the screen shown, at the place the press would open, opens nothing
 * (openScreen's own check), so rapid presses add a step for each change of tab and none for the same tab again.
 *
 * Library opens the top of Browse; Photos the Grid on the view it showed last, or the whole library before any;
 * Libraries the list of libraries, where another library is another page; Filter the Filter sheet over the Grid
 * (mobile-filter.js openFilter), a step of its own, so Back closes it.
 *
 * The bar is drawn from the screen shown after every step (a press, Back, Forward, a reload), the tab of that screen
 * lit. It is hidden over the Photo screen, the picture on its own canvas with its own Back, and on a page on no
 * library, where the list of libraries is all there is.
 */
import { state } from './mobile-state.js';
import { openScreen } from './mobile-screens.js';
import { viewOf } from './mobile-browse.js';
import { openFilter } from './mobile-filter.js';

/** What a press on each tab opens, by its name: a screen at its place, or the Filter sheet. */
const OPENS = {
    browse: () => openScreen('browse', null),
    grid: () => openScreen('grid', state.view || viewOf({ kind: 'all' })),
    filter: openFilter,
    library: () => openScreen('library', null),
};

/** The bar for the screen shown: that screen's tab lit, or no bar. */
export function drawTabs() {
    const bar = document.getElementById('tabs');
    bar.classList.toggle('hidden', !state.library || state.screen === 'photo');
    for (const tab of bar.querySelectorAll('[data-tab]')) {
        if (tab.dataset.tab === state.screen) tab.setAttribute('aria-current', 'page');
        else tab.removeAttribute('aria-current');
    }
}

/** A press on a tab opens its screen. */
export function wireTabs() {
    document.getElementById('tabs').addEventListener('click', (event) => {
        const tab = event.target.closest('[data-tab]');
        const opens = tab && OPENS[tab.dataset.tab];
        if (opens) opens();
    });
}
