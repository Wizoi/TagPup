// Re-examine this folder: name the faces in a folder, and the folders under it, that now
// clearly match somebody already named (POST /api/folder/automatch). It asks first, with
// what a dry run found -- how many faces, in how many photos, and whose -- and only then
// names them. The apply decides again on the server, so a face named, excluded or renamed
// in between is not named, and the page says when the two differ.
import { api } from './common/api.js';
import { replaceContent } from './common/dom.js';
import { state } from './state.js';
import { modeSelect, showMatchedToggle } from './elements.js';
import { selectPhoto } from './faces-strip.js';

//: How many people the question names before it says "and N more".
const PEOPLE_SHOWN = 8;

export const REEXAMINE_TITLE = 'Re-examine this folder: name the faces that now clearly match somebody already named';

function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
}

/** "Rowan Thackeray 12, Wren Halloway 3, and 4 more": most faces first, then by name. */
export function whoseFaces(people) {
    const ranked = Object.entries(people || {})
        .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]));
    const shown = ranked.slice(0, PEOPLE_SHOWN).map(([name, n]) => `${name} ${n}`);
    const rest = ranked.length - shown.length;
    return shown.join(', ') + (rest > 0 ? `, and ${rest} more` : '');
}

/** What the page asks before naming anybody, from a dry run's answer. */
export function reexamineQuestion(plan) {
    return `Name ${plural(plan.faces, 'face', 'faces')} in ${plural(plan.photos, 'photo', 'photos')}`
        + ` in this folder and the folders under it (${whoseFaces(plan.people)})?`;
}

async function ask(folderPath, dryRun) {
    const res = await api.fetch('/api/folder/automatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: folderPath, dry_run: dryRun }),
    });
    let data = null;
    try {
        data = await res.json();
    } catch (e) {
        data = null;
    }
    if (!res.ok || !data || !data.success) {
        throw new Error((data && (data.error || data.description)) || `the server answered ${res.status}`);
    }
    return data;
}

// The photos the apply named faces in, wherever the list shows them: a folder's photos
// can sit under several years, and the folders under it are groups of their own.
function showWhatChanged(done) {
    const named = new Set(done.photos_named || []);
    const remaining = done.remaining_counts || {};
    const showMatched = !!(showMatchedToggle && showMatchedToggle.checked);
    const groups = new Set();
    state.allPhotos.forEach(p => {
        if (!named.has(p.path)) return;
        const left = remaining[p.path] || 0;
        p.matched_count = (p.matched_count || 0) + (p.unmatched_count - left);
        p.unmatched_count = left;
        if (p.badgeEl) p.badgeEl.textContent = `${p.unmatched_count} unmatched`;
        if (p.badgeMatchedEl) p.badgeMatchedEl.textContent = `${p.matched_count} matched`;
        if (p.unmatched_count === 0 && p.liEl) {
            // Show matched keeps a finished photo in the list, marked done.
            if (modeSelect.value === 'folder-match' && !showMatched) {
                p.liEl.style.display = 'none';
            } else {
                p.liEl.classList.add('all-matched');
            }
        }
        if (p.folderGroup) groups.add(p.folderGroup);
    });
    groups.forEach(group => {
        const left = group.photos.reduce((sum, p) => sum + p.unmatched_count, 0);
        if (group.countEl) group.countEl.textContent = ` (${left})`;
        if (left === 0 && group.btnEl && !showMatched) group.btnEl.style.display = 'none';
    });
    if (state.activePhotoPath && named.has(state.activePhotoPath)) {
        selectPhoto(state.activePhotoPath);
    }
}

/**
 * What the apply did, said only when it is not what the question said it would do: in
 * number, or in whose faces -- the same count can be other people (docs/findings.md,
 * #646). Faces left for a renamed person are said as their own share, and anything
 * they do not account for is said as well.
 */
export function differences(plan, done) {
    const asked = plan.people || {};
    const named = done.people || {};
    const changed = [...new Set([...Object.keys(asked), ...Object.keys(named)])]
        .filter(name => (asked[name] || 0) !== (named[name] || 0))
        .sort((a, b) => a.localeCompare(b));
    if (done.faces === plan.faces && changed.length === 0) return null;
    const said = [`Named ${plural(done.faces, 'face', 'faces')} in ${plural(done.photos, 'photo', 'photos')};`
        + ` it asked about ${plural(plan.faces, 'face', 'faces')}.`];
    if (changed.length) {
        const shown = changed.slice(0, PEOPLE_SHOWN)
            .map(name => `${name} ${asked[name] || 0} to ${named[name] || 0}`);
        const rest = changed.length - shown.length;
        said.push('Changed: ' + shown.join(', ') + (rest > 0 ? `, and ${rest} more` : '') + '.');
    }
    // Only the faces left for a rename since the dry run: those it already left out were
    // never in the question (docs/findings.md, #657).
    const renamed = Math.max(0, (done.renamed || 0) - (plan.renamed || 0));
    if (renamed) {
        said.push(`${plural(renamed, 'face was', 'faces were')} left because their person was renamed meanwhile.`);
    }
    // Wholly the renamed share: the faces asked about, less those, and nobody else's count moved.
    const onlyRenamed = renamed && done.faces + renamed === plan.faces
        && changed.every(name => (named[name] || 0) <= (asked[name] || 0));
    if (!onlyRenamed) {
        said.push('Faces were named, excluded or unmatched since it asked, or names given since matched more.');
    }
    return said.join(' ');
}

/**
 * Re-examine the folder of a group in the list. One at a time on the page: a second
 * press, here or on another folder, while one is being asked about or written does
 * nothing.
 */
export async function reexamineFolder(folderGroup, btn) {
    if (state.reexamining) return;
    state.reexamining = folderGroup.name;
    btn.disabled = true;
    // What the button showed, put back as it was: its own nodes, not a copy as markup.
    const originalContent = [...btn.childNodes];
    btn.textContent = '⏳';
    btn.title = 'Re-examining this folder...';
    try {
        const plan = await ask(folderGroup.name, true);
        if (!plan.faces) {
            alert(plan.renamed
                ? 'No face can be named now: the people they matched were renamed. Try again.'
                : 'No face in this folder clearly matches anybody named yet.');
            return;
        }
        if (!confirm(reexamineQuestion(plan))) return;
        const done = await ask(folderGroup.name, false);
        showWhatChanged(done);
        const differs = differences(plan, done);
        if (differs) alert(differs);
    } catch (err) {
        console.error('Error re-examining a folder:', err);
        alert('Could not re-examine this folder: ' + err.message);
    } finally {
        state.reexamining = null;
        btn.disabled = false;
        replaceContent(btn, ...originalContent);
        btn.title = REEXAMINE_TITLE;
    }
}
