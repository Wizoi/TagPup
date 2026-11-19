/**
 * The phone's page (docs/plans/phone-viewer.md): this only wires its parts, and starts it at the very end, as the
 * desktop page's main.js does. Each feature is a mobile-*.js module; what changes is `state` (mobile-state.js).
 *
 * It has no gear and no link to TagTuner: through Tailscale Serve the other app is not at an address the server can
 * build from the request (docs/plans/phone-viewer.md).
 */
import { wireRootsBanner } from './common/roots-banner.js';
import { libraryOfThePage } from './common/library.js';
import { state } from './mobile-state.js';
import { openScreen, startScreens, whenShown, wireScreens } from './mobile-screens.js';
import { showLibraries, wireLibraries } from './mobile-library.js';
import { linkedView, showBrowse, wireBrowse } from './mobile-browse.js';
import { forgetGrid, showGrid, wireGrid } from './mobile-grid.js';
import { showPhoto } from './mobile-photo.js';

document.addEventListener('DOMContentLoaded', () => {
    wireRootsBanner();
    wireScreens();
    wireLibraries();
    whenShown('library', showLibraries);
    wireBrowse();
    whenShown('browse', forgetGrid);
    whenShown('browse', showBrowse);
    wireGrid();
    whenShown('grid', showGrid);
    whenShown('photo', showPhoto);
    document.getElementById('bar-library').addEventListener('click', () => openScreen('library'));

    // Last: the view and the library the URL names (the library remembered as the one opened last), then the first screen.
    state.linked = linkedView();
    state.library = libraryOfThePage();
    startScreens();
});
