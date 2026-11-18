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

document.addEventListener('DOMContentLoaded', () => {
    wireRootsBanner();
    wireScreens();
    wireLibraries();
    whenShown('library', showLibraries);
    document.getElementById('bar-library').addEventListener('click', () => openScreen('library'));

    // Last: the library the URL names (remembered as the one opened last), then the first screen.
    state.library = libraryOfThePage();
    startScreens();
});
