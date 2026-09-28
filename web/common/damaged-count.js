/**
 * How many of the library's photos were found damaged, in each page's header: a small
 * badge -- "13 unreadable photos" -- that opens the Activity page's Needs attention, where
 * each is listed with why and its folder. Nothing at all when there are none.
 *
 * The indexer finds them: a photo whose picture does not decode, which it does not index
 * or write to, and one that decodes but ends in zero bytes, possibly an incomplete copy
 * (tagpup.services.damaged_photos). They were silent: the photo was a gap in the folder,
 * and nothing said there was anything wrong. A file replaced since has left the count.
 *
 * Counts only (GET /api/damaged-photos): which photos they are is the Activity page's to
 * say, to this PC.
 */
import { api, libraryIn } from './api.js';

function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
}

/** "13 unreadable photos, 1 possibly incomplete copy", or '' for none. */
export function damagedCountText(found) {
    const unreadable = Number(found && found.unreadable) || 0;
    const incomplete = Number(found && found.incomplete) || 0;
    const said = [];
    if (unreadable) said.push(plural(unreadable, 'unreadable photo', 'unreadable photos'));
    if (incomplete) said.push(plural(incomplete, 'possibly incomplete copy', 'possibly incomplete copies'));
    return said.join(', ');
}

/** Ask how many, and show it in `badge` (a link to the Activity page); hidden for none. */
export function showDamagedCount(badge) {
    if (!badge || !libraryIn(window.location.pathname)) return Promise.resolve(null);
    return api.json('/api/damaged-photos').then(found => {
        const text = damagedCountText(found);
        badge.textContent = text ? `⚠ ${text}` : '';
        badge.title = text ? 'Found damaged in this library: see which, why, and where, on the Activity page' : '';
        badge.classList.toggle('hidden', !text);
        return found;
    }).catch(err => {
        console.error('Could not ask how many photos were found damaged:', err);
        return null;
    });
}
