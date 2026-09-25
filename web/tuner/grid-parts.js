// The Identify grid on screen: its headings and tab counts, and faces leaving and
// coming back without a rebuild.
import { state } from './state.js';
import {
    matchingFacesGrid, matchingPersonCount, modeSelect, tabLowConf, tabMatches, tabOutliers,
} from './elements.js';
import { BAND_OF } from './rules.js';
import { updateMatchingSelectionUI } from './selection.js';

export function updateTabLabels() {
    const tabMatches = document.getElementById('tab-matches');
    const tabOutliers = document.getElementById('tab-outliers');
    const tabLowConf = document.getElementById('tab-low-conf');
    if (!tabMatches || !tabOutliers) return;
    
    let filteredFaces = state.activePersonFaces;
    
    if (modeSelect.value === 'unmatched-faces') {
        // One rule for which band a candidate is in, used by the counts here and
        // by the render below. They disagreeing is how a tab comes to say a number
        // it then does not show.
        const bandOf = (f) => BAND_OF[f.band] || 'rest';
        const high = filteredFaces.filter(f => bandOf(f) === 'high');
        const lower = filteredFaces.filter(f => bandOf(f) === 'lower');
        
        // Everything the server sent that clustered with nothing. It ranks these
        // last rather than withholding them, because a face that forms no group
        // is still a face somebody may recognise -- often the only ones there are.
        const unclustered = filteredFaces.filter(f => bandOf(f) === 'rest');

        tabMatches.textContent = `Likely (${high.length})`;
        tabOutliers.textContent = `Possible (${lower.length})`;
        if (tabLowConf) {
            // Named for what it holds, which depends on whether there is anyone
            // to rank these faces against. Neither name is "Ungrouped": the
            // sidebar already has a bucket by that name meaning something else
            // entirely -- faces whose photo names somebody who has only that one
            // candidate in the whole library.
            const isRanked = filteredFaces.some(f => f.person_similarity !== undefined);
            tabLowConf.textContent = isRanked
                ? `Unlikely (${unclustered.length})`
                : `Unclustered (${unclustered.length})`;
            tabLowConf.title = isRanked
                ? 'These are in photos that name this person, but they do not look '
                  + 'much like the faces already named as them (under 60%). Most of '
                  + 'a crowd photo lands here, and that is the point -- it is what '
                  + 'is left after the likely ones are lifted out.'
                : 'Faces that resembled nothing else, so grouping left them on '
                  + 'their own. Nobody is named for this person yet, so there is '
                  + 'nothing to rank them against.';
            tabLowConf.classList.toggle('hidden', unclustered.length === 0);
        }
    } else {
        // The server decides which names look wrong (tagpup.core.clustering.looks_wrong).
        const standards = filteredFaces.filter(f => !f.possibly_wrong);
        const outliers = filteredFaces.filter(f => f.possibly_wrong);
        
        tabMatches.textContent = `Matches (${standards.length})`;
        tabOutliers.textContent = `Outliers (${outliers.length})`;
    }
}

/**
 * Move the highlight to whichever Identify Faces tab is being shown.
 *
 * The tabs carry their own click handlers; this is for when the code picks the
 * tab instead of the person, so the highlight cannot disagree with the grid.
 */
export function syncIdentifyTabHighlight(tab) {
    const els = {
        high: document.getElementById('tab-matches'),
        lower: document.getElementById('tab-outliers'),
        low: document.getElementById('tab-low-conf'),
    };
    Object.entries(els).forEach(([name, el]) => {
        if (el) el.classList.toggle('active', name === tab);
    });
}

/** Recount the heading from what is actually on screen, after faces have left. */
function updateFacesHeadingCount() {
    if (!matchingPersonCount) return;
    const shown = matchingFacesGrid.querySelectorAll('[data-face-id]').length;
    const windowNote = (state.activeFacesTotal && state.activeTab === 'low')
        ? ` — first ${shown} of ${state.activeFacesTotal}, more appear as you clear these`
        : '';
    matchingPersonCount.textContent =
        `${shown} ${state.activeFacesLabel} face${shown !== 1 ? 's' : ''}` + windowNote;
}

export function groupHeadingText(title, isUnclustered, numFaces, numPhotos) {
    const faceWord = `${numFaces} face${numFaces !== 1 ? 's' : ''}`;
    const photoWord = `${numPhotos} photo${numPhotos !== 1 ? 's' : ''}`;
    return isUnclustered
        ? `Unclustered — ${faceWord} that resembled nothing else, from ${photoWord}`
        : `${title} (${faceWord} in ${photoWord})`;
}

/**
 * The faces a group holds now, read from the cards still in it.
 *
 * Faces leave a group in place -- assigned, excluded -- without the group being
 * drawn again, so the list it was drawn with goes stale the moment one does. The
 * cluster buttons used to act on that list: Ignore Cluster excluded faces that had
 * just been named by hand, wiping their names, and "Assign N" named faces that had
 * just been excluded. The cards are what the person is looking at, so they decide.
 */
export function sectionFaceIds(section) {
    return [...section.querySelectorAll('[data-face-id]')]
        .map(card => Number(card.dataset.faceId));
}

export function sectionPhotoCount(section) {
    const photos = new Set();
    section.querySelectorAll('[data-face-id]')
        .forEach(card => photos.add(card.dataset.photoPath || ''));
    return photos.size;
}

export function notAClusterNote() {
    const note = document.createElement('span');
    note.className = 'not-a-cluster-note';
    note.textContent = 'these are unrelated — handle them one at a time';
    note.title = 'Face grouping puts together the faces that resemble '
        + 'each other. These resembled nothing, so they are listed '
        + 'together only because they have nowhere else to go. Select '
        + 'the ones you recognise and use Assign Selected, or Exclude.';
    return note;
}

export function labelClusterAssign(button, count) {
    const guessName = button.dataset.guessName || '';
    button.textContent = `✓ Assign ${count}`;
    button.title = `Assign all ${count} face(s) in this `
        + `group to ${guessName} now. You can undo it straight after.`;
}

/**
 * Take faces out of the grid that is already on screen.
 *
 * Assigning or ignoring used to remove the handful of cards involved and then call
 * renderPersonFaces, which throws the whole grid away and builds it again -- for
 * Unknown Faces that is twenty-four thousand cards, every one of them cleared,
 * destroyed and recreated to account for five leaving. Ignoring one cluster of five
 * took about ten seconds, all of it in the browser.
 *
 * Nothing about the other cards changed, so nothing about them needs rebuilding.
 * What does change is the heading of any group that lost faces, and whether that
 * group still has any, so that is all this touches.
 */
export function removeFacesFromGrid(faceIds) {
    const leaving = new Set(faceIds);
    const touched = new Set();

    leaving.forEach(id => {
        const card = matchingFacesGrid.querySelector(`[data-face-id="${id}"]`);
        if (!card) return;
        const section = card.closest('.matching-group-section');
        // Where it sat, for Undo.
        state.removedFromGrid.set(id, {
            card,
            grid: card.parentElement,
            next: card.nextElementSibling,
            section,
            sectionParent: section ? section.parentElement : null,
            sectionNext: section ? section.nextElementSibling : null,
            face: state.renderedFacesById.get(id),
            suggestion: state.suggestionByFaceId.get(id),
        });
        card.remove();
        if (section) touched.add(section);
    });

    touched.forEach(section => {
        const cards = section.querySelectorAll('[data-face-id]');
        if (!cards.length) {
            // Nobody left in this group. The heading, its Assign Cluster button
            // and its Ignore Cluster button have nothing to act on.
            section.remove();
            return;
        }
        if (modeSelect.value !== 'unmatched-faces') return;

        // One face left is not a cluster any more -- the server's
        // _dissolve_stranded_clusters moves such a survivor to the Unclustered
        // pile on the next load. Treat it that way now: no heading claiming a
        // group, and no button acting on "all" of one face.
        if (cards.length === 1 && !section.dataset.unclustered) {
            section.dataset.unclustered = '1';
            section.querySelectorAll('.cluster-action, .cluster-suggestion')
                .forEach(el => el.remove());
        }
        refreshSectionHeading(section);
    });

    state.renderedFaceOrder = state.renderedFaceOrder.filter(id => !leaving.has(id));
    state.selectedFaceIds = state.selectedFaceIds.filter(id => !leaving.has(id));
    leaving.forEach(id => state.suggestionByFaceId.delete(id));
    updateMatchingSelectionUI();
    updateFacesHeadingCount();
}

/** A group's heading and one-click assign, recounted from the cards it holds now. */
function refreshSectionHeading(section) {
    const titleSpan = section.querySelector('.matching-group-title');
    if (!titleSpan || modeSelect.value !== 'unmatched-faces') return;
    const cards = section.querySelectorAll('[data-face-id]');
    const note = titleSpan.querySelector('.not-a-cluster-note')
        || (section.dataset.unclustered ? notAClusterNote() : null);
    titleSpan.textContent = groupHeadingText(
        section.dataset.groupTitle || '',
        Boolean(section.dataset.unclustered),
        cards.length,
        sectionPhotoCount(section));
    if (note) titleSpan.appendChild(note);

    // The one-click assign says how many it will assign. While a request is
    // in flight it says so instead, and puts its own count back when done.
    const accept = section.querySelector('.cluster-suggestion-assign');
    if (accept && !accept.disabled) labelClusterAssign(accept, cards.length);
}

/**
 * Put faces an Undo has just restored back where they were in the grid.
 *
 * Only cards this grid took out: if the grid has been rebuilt since -- another
 * person chosen, another tab -- they are not on screen to put back, and the
 * restored faces show the next time that grid is loaded.
 */
export function putFacesBack(faceIds) {
    const touched = new Set();
    // Last out, first back: each goes in before the card that followed it, which
    // may itself be one of these, already back.
    [...faceIds].reverse().forEach(id => {
        const saved = state.removedFromGrid.get(id);
        if (!saved) return;
        state.removedFromGrid.delete(id);
        const { card, grid, next, section, sectionParent, sectionNext, face, suggestion } = saved;
        if (section && !section.isConnected && sectionParent && sectionParent.isConnected) {
            sectionParent.insertBefore(section,
                sectionNext && sectionNext.parentElement === sectionParent ? sectionNext : null);
        }
        if (!grid || !grid.isConnected) return;
        grid.insertBefore(card, next && next.parentElement === grid ? next : null);
        card.classList.remove('selected');
        if (section) touched.add(section);
        if (face && !state.activePersonFaces.some(f => f.id === id)) state.activePersonFaces.push(face);
        if (face) state.renderedFacesById.set(id, face);
        if (!state.renderedFaceOrder.includes(id)) state.renderedFaceOrder.push(id);
        if (suggestion) state.suggestionByFaceId.set(id, suggestion);
    });
    touched.forEach(refreshSectionHeading);
    updateTabLabels();
    updateMatchingSelectionUI();
    updateFacesHeadingCount();
}
