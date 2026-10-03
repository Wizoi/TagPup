// TagPup's page: whether the folder open is being looked at without being added to the
// library (membership.js asks, and the choice is `state.justLooking`), and which library
// the page works in. A leaf module: the writers and the editors ask it too, without
// importing the dialog.
import { libraryIn } from './common/api.js';
import { samePath } from './common/paths.js';
import { state } from './state.js';

/** The library the page works in: the first part of its address. */
export function libraryName() {
    return libraryIn(window.location.pathname);
}

/** Is the folder open one the library does not hold, looked at without adding it? */
export function isJustLooking() {
    return Boolean(state.justLooking && state.scannedFolder && samePath(state.justLooking, state.scannedFolder));
}
