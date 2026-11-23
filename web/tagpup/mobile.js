/**
 * The phone's page (docs/plans/phone-viewer.md): this only wires its parts, and starts it at the very end, as the
 * desktop page's main.js does. Each feature is a mobile-*.js module; what changes is `state` (mobile-state.js).
 *
 * It has no gear and no link to TagTuner: through Tailscale Serve the other app is not at an address the server can
 * build from the request (docs/plans/phone-viewer.md).
 */
import { wireRootsBanner } from './common/roots-banner.js';
import { wireImageZoom } from './common/image-zoom.js';
import { libraryOfThePage } from './common/library.js';
import { state } from './mobile-state.js';
import { startScreens, whenShown, wireScreens } from './mobile-screens.js';
import { drawTabs, wireTabs } from './mobile-tabs.js';
import { showLibraries, wireLibraries } from './mobile-library.js';
import { linkedView, showBrowse, wireBrowse } from './mobile-browse.js';
import { forgetGrid, showGrid, wireGrid } from './mobile-grid.js';
import { filterFollows, wireFilter } from './mobile-filter.js';
import { leavePhoto, showPhoto, wirePhoto } from './mobile-photo.js';
import { leaveEdit, wireEdit } from './mobile-edit.js';

document.addEventListener('DOMContentLoaded', () => {
    wireRootsBanner();
    wireScreens();
    wireTabs();
    for (const name of ['library', 'browse', 'grid', 'photo']) whenShown(name, drawTabs);
    wireLibraries();
    whenShown('library', showLibraries);
    wireBrowse();
    whenShown('browse', forgetGrid);
    whenShown('browse', showBrowse);
    wireGrid();
    wireFilter();
    whenShown('grid', filterFollows);    // the marks of the view first: the Grid's count line reads them
    whenShown('grid', showGrid);
    wireImageZoom();
    wirePhoto();
    whenShown('photo', showPhoto);
    for (const name of ['library', 'browse', 'grid']) whenShown(name, leavePhoto);
    wireEdit();
    for (const name of ['library', 'browse', 'grid', 'photo']) whenShown(name, leaveEdit);    // a swipe shows 'photo' anew

    // Last: the view and the library the URL names (the library remembered as the one opened last), then the first screen.
    state.linked = linkedView();
    state.library = libraryOfThePage();
    startScreens();
});
