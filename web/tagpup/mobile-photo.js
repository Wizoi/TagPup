/**
 * The phone's Photo screen (docs/plans/phone-viewer.md, Screens 4): one photo of the Grid's view, large, read-only (the
 * edit sheet is the next cut). Its place in history is { id, at }: the photo's id and its index in the Grid's view. A
 * swipe (web/common/swipe.js) moves through the view and writes the photo it lands on into the same step, so a reload
 * opens that photo and Back is the Grid with its card in view (mobile-grid.js), however many photos were swiped through.
 *
 * The picture is the photo's file at 1024 px (web/common/file-addresses.js, by the card's `mtime`); a tap opens the file whole in the shared zoom (web/common/image-zoom.js),
 * where the phone's own pinch enlarges it. A clip plays from /api/video-file, its poster shown until it does. Which file
 * is the card the Grid holds, or, for a card not asked yet (a window still loading, a reload), the card read by id; a
 * reply for a photo already swiped past is dropped. A missing file, a share that does not answer, a clip the phone
 * cannot play: a sentence, and the swipe goes on.
 */
import { api } from './common/api.js';
import { photoFileUrl, videoRoute } from './common/file-addresses.js';
import { closeImageZoom, openImageZoom } from './common/image-zoom.js';
import { enableSwipeNavigation } from './common/swipe.js';
import { state } from './mobile-state.js';
import { replacePlace, samePlace } from './mobile-screens.js';

const byId = (id) => document.getElementById(id);

/** Where the photo of `place` is in the view held, or -1: its `at` while that is still its id, else looked for. */
function indexIn(lib, place) {
    if (!lib || !place || !Number.isInteger(place.id)) return -1;
    if (Number.isInteger(place.at) && lib.ids[place.at] === place.id) return place.at;
    return lib.ids.indexOf(place.id);
}

/** Say why the photo is not shown, or '' for nothing to say. */
function say(text) {
    byId('photo-problem').textContent = text;
    byId('photo-problem').classList.toggle('hidden', !text);
}

/** Let go of the clip: paused, its stream dropped. */
function stopClip() {
    const clip = byId('photo-clip');
    clip.classList.add('hidden');
    if (!clip.getAttribute('src')) return;
    clip.pause();
    clip.removeAttribute('src');
    delete clip.dataset.route;
    clip.load();
}

/** No picture and no clip: between a swipe and its card, or a photo that cannot be shown. */
function blank() {
    stopClip();
    const picture = byId('photo-picture');
    picture.classList.add('hidden');
    picture.removeAttribute('src');
}

/** Show `card`: its picture, or its clip with its poster; a file that is gone says so. */
function draw(card) {
    state.photo = card;
    byId('photo-name').textContent = card.name || '';
    if (card.stale === 'missing') {
        blank();
        say('This file is not where the library last saw it: moved or deleted on the PC.');
        return;
    }
    say('');
    const picture = byId('photo-picture');
    if (card.media !== 'video') {
        stopClip();
        picture.src = photoFileUrl(card, 1024);
        picture.classList.remove('hidden');
        return;
    }
    picture.classList.add('hidden');
    picture.removeAttribute('src');
    const clip = byId('photo-clip');
    clip.classList.remove('hidden');
    const route = videoRoute(card);
    if (clip.dataset.route === route && !clip.error) return;    // the same clip again: it plays on
    clip.dataset.route = route;
    clip.poster = card.thumb ? api.image(card.thumb) : '';
    clip.src = api.url(route);
}

/** Show the photo of the Photo screen's place, and where it is in the view. */
export function showPhoto() {
    const place = state.place;
    const lib = state.photos;
    const at = indexIn(lib, place);
    byId('photo-at').textContent = at >= 0
        ? `${(at + 1).toLocaleString('en-US')} of ${lib.ids.length.toLocaleString('en-US')}` : '';
    const card = lib && place ? lib.cards.get(place.id) : null;
    if (card) {
        draw(card);
        return;
    }
    state.photo = null;
    blank();
    say('');
    byId('photo-name').textContent = '';
    if (!place || !Number.isInteger(place.id)) return;
    const current = () => state.screen === 'photo' && samePlace(state.place, place);    // not swiped past, nor left
    api.fetch(`/api/library/cards?ids=${place.id}`)
        .then((res) => api.answered(res, 'Could not load this photo.'))    // a refusal (500, 503) is a failure, not "gone"
        .then((body) => {
            if (!current()) return;
            const read = body && Array.isArray(body.cards) ? body.cards.find((each) => each && each.id === place.id) : null;
            if (read) draw(read);
            else say('This photo is no longer in the library.');
        })
        .catch(() => {
            if (current()) say('Could not load this photo. Swipe on, or come back to it later.');
        });
}

/** The next photo of the view (1) or the one before (-1); nothing at its first or last, or with no view held (a reload). */
export function stepPhoto(delta) {
    if (state.screen !== 'photo') return;
    const lib = state.photos;
    const at = indexIn(lib, state.place);
    const next = at + delta;
    if (at < 0 || next < 0 || next >= lib.ids.length) return;
    // The photo swiped to: its card is brought into view when the Grid is shown again (one opened by a tap is in view).
    lib.activeId = lib.ids[next];
    replacePlace({ id: lib.ids[next], at: next });
}

/** Another screen is shown: the clip stops and the zoom closes. */
export function leavePhoto() {
    stopClip();
    closeImageZoom();
}

/** The swipe, a tap to zoom, and what a picture or a clip that will not load says. */
export function wirePhoto() {
    const stage = byId('photo-stage');
    const picture = byId('photo-picture');
    const clip = byId('photo-clip');
    enableSwipeNavigation(stage, stepPhoto);
    picture.addEventListener('click', () => {
        if (stage.dataset.dragged === 'true') {
            delete stage.dataset.dragged;    // that was a swipe
            return;
        }
        if (state.photo && state.photo.media !== 'video') {
            openImageZoom(photoFileUrl(state.photo), { preview: picture.getAttribute('src') || '', opener: picture });
        }
    });
    picture.addEventListener('error', () => {
        if (!picture.getAttribute('src') || state.screen !== 'photo') return;
        picture.classList.add('hidden');
        say('Could not load this photo: the PC could not read its file. Swipe on, or come back to it later.');
    });
    clip.addEventListener('error', () => {
        if (!clip.getAttribute('src') || state.screen !== 'photo') return;
        say('This clip will not play here. Play it on the PC.');
    });
}
