// Review Tags.
import { api } from './common/api.js';
import { buildElement, replaceContent } from './common/dom.js';
import { tagProblem } from './common/vocabulary.js';
import { state } from './state.js';
import { listStats, peopleSort, photoList, photoSearch } from './elements.js';

const tagViewName = document.getElementById('tag-view-name');
const tagViewSummary = document.getElementById('tag-view-summary');
const tagViewPlaceholder = document.getElementById('tag-view-placeholder');
const tagPhotoGrid = document.getElementById('tag-photo-grid');
const btnTagRename = document.getElementById('btn-tag-rename');
const btnTagMerge = document.getElementById('btn-tag-merge');
const btnTagRetire = document.getElementById('btn-tag-retire');

// ---- Review Tags -----------------------------------------------------------
//
// A tagged photo is what the suggester learns the next photo from, so a
// misspelling spreads exactly as a wrong name does -- and until now there was no
// view in which "where is this tag, and what does it touch" could be asked at
// all. Finding one took a database query and an ExifTool sweep.
//
// The sidebar is the same one Review People uses; a tag takes a person's place.

//: Buckets pinned above the alphabet, the way Unknown Faces is pinned in the
//: people list. Each answers a question the flat list cannot: which tags have lost
//: their hierarchy, which have never been confirmed by a second photo, which are
//: offerable while describing nothing, and which people are written as bare leaves.
const TAG_BUCKETS = [
    ['flat', 'No hierarchy'],
    ['used_once', 'Used once'],
    ['unused', 'On no photo'],
    ['people_without_a_path', 'People missing a path'],
];

export function loadTags() {
    listStats.textContent = 'Loading tags...';
    photoList.innerHTML = '';
    return api.json('/api/tags/list')
        .then(data => {
            state.allTags = data.tags || [];
            state.tagBuckets = data.buckets || {};
            renderTagList();
        })
        .catch(err => {
            console.error('Error loading tags:', err);
            listStats.textContent = 'Could not load tags';
        });
}

/** Tags in the order the sort dropdown asks for, filtered by the search box. */
function visibleTags() {
    const query = (photoSearch.value || '').toLowerCase();
    const matching = state.allTags.filter(t => t.tag.toLowerCase().includes(query));
    const byName = (a, b) => a.tag.toLowerCase().localeCompare(b.tag.toLowerCase());
    if (peopleSort && peopleSort.value === 'name') return matching.sort(byName);
    return matching.sort((a, b) => (b.count - a.count) || byName(a, b));
}

export function renderTagList() {
    photoList.innerHTML = '';
    const tags = visibleTags();
    listStats.textContent = `${state.allTags.length} tag${state.allTags.length === 1 ? '' : 's'}`;

    TAG_BUCKETS.forEach(([key, label]) => {
        const members = state.tagBuckets[key] || [];
        if (members.length === 0) return;
        photoList.appendChild(tagRow(label, members.length, {
            bucket: key,
            hint: 'A bucket, not a tag',
        }));
    });

    tags.forEach(tag => {
        photoList.appendChild(tagRow(tag.tag, tag.count, { tag }));
    });
}

function tagRow(label, count, { tag, bucket, hint } = {}) {
    const li = document.createElement('li');
    li.className = 'photo-item';
    if (bucket) {
        li.classList.add('tag-bucket-item');
        li.dataset.bucket = bucket;
        if (state.activeBucket === bucket) li.classList.add('active');
    } else {
        li.dataset.tag = tag.tag;
        if (state.activeTag === tag.tag) li.classList.add('active');
    }

    const title = document.createElement('div');
    title.className = 'photo-title';
    title.textContent = label;
    if (hint) title.title = hint;
    li.appendChild(title);

    const badge = document.createElement('span');
    badge.className = 'photo-badge';
    badge.textContent = count === 0 ? 'no photos' : `${count} photo${count === 1 ? '' : 's'}`;
    li.appendChild(badge);

    // What is wrong with it, where something is: a tag with no hierarchy, or one
    // the vocabulary offers while it describes nothing.
    if (tag) {
        const marks = [];
        if (tag.count === 0) marks.push('orphan');
        else if (tag.flat) marks.push('flat');
        if (!tag.has_embedding && tag.count > 0) marks.push('not cached');
        if (marks.length) {
            const note = document.createElement('span');
            note.className = 'tag-item-note';
            note.textContent = marks.join(' · ');
            li.appendChild(note);
        }
    }

    li.addEventListener('click', () => {
        if (bucket) {
            state.activeBucket = bucket;
            state.activeTag = null;
            showBucket(bucket);
        } else {
            state.activeBucket = null;
            state.activeTag = tag.tag;
            showTag(tag.tag);
        }
        renderTagList();
    });
    return li;
}

function showBucket(key) {
    const label = (TAG_BUCKETS.find(b => b[0] === key) || [])[1] || key;
    const members = state.tagBuckets[key] || [];
    tagViewPlaceholder.classList.add('hidden');
    tagViewName.textContent = label;
    tagViewSummary.textContent =
        `${members.length} tag${members.length === 1 ? '' : 's'}. Pick one to see its photos.`;
    setTagActionsEnabled(false);

    tagPhotoGrid.innerHTML = '';
    const list = document.createElement('div');
    list.className = 'tag-bucket-members';
    members.forEach(name => {
        const chip = document.createElement('button');
        chip.className = 'tag-bucket-member';
        chip.textContent = name;
        chip.addEventListener('click', () => {
            state.activeBucket = null;
            state.activeTag = name;
            showTag(name);
            renderTagList();
        });
        list.appendChild(chip);
    });
    tagPhotoGrid.appendChild(list);
}

function showTag(tag) {
    const record = state.allTags.find(t => t.tag === tag);
    tagViewPlaceholder.classList.add('hidden');
    tagViewName.textContent = tag;
    tagViewSummary.textContent = 'Loading…';
    setTagActionsEnabled(true);
    tagPhotoGrid.innerHTML = '';

    api.json(`/api/tags/photos?tag=${encodeURIComponent(tag)}`)
        .then(data => {
            if (state.activeTag !== tag) return;   // a slower reply for an older click
            const photos = data.photos || [];
            const parts = [`${data.total} photo${data.total === 1 ? '' : 's'}`];
            if (record && record.flat) parts.push('no hierarchy');
            if (record && !record.in_taxonomy) parts.push('not in the taxonomy');
            if (record && record.has_embedding) parts.push('cached for suggestions');
            tagViewSummary.textContent = parts.join(' · ');

            if (photos.length === 0) {
                replaceContent(tagPhotoGrid, buildElement('p', {
                    className: 'tag-empty',
                    text: 'No photo carries this tag. It can still be suggested, which is usually a reason to retire it.',
                }));
                return;
            }
            photos.forEach(photo => tagPhotoGrid.appendChild(tagPhotoCard(photo)));
        })
        .catch(err => {
            console.error('Error loading photos for tag:', err);
            tagViewSummary.textContent = 'Could not load its photos';
        });
}

function tagPhotoCard(photo) {
    const card = document.createElement('div');
    card.className = 'tag-photo-card';
    card.title = photo.path;

    const img = document.createElement('img');
    img.src = api.image(`/api/photo-file?path=${encodeURIComponent(photo.path)}&thumb=1`);
    img.alt = photo.filename;
    img.loading = 'lazy';
    card.appendChild(img);

    const name = document.createElement('div');
    name.className = 'tag-photo-name';
    name.textContent = photo.filename;
    card.appendChild(name);
    return card;
}

function setTagActionsEnabled(enabled) {
    [btnTagRename, btnTagMerge, btnTagRetire].forEach(btn => {
        if (btn) btn.disabled = !enabled;
    });
}

/**
 * Change a tag everywhere it lives, after showing what that means.
 *
 * The server is asked twice: once without `apply`, which changes nothing and
 * reports what it would touch, and again to write it. A tag can sit on hundreds of
 * photo files, and a rename rewrites every one of them -- the plan is the last
 * point at which that costs nothing.
 */
function changeTag({ title, prompt, needsTarget, retire }) {
    if (!state.activeTag) return;
    let target = '';
    if (needsTarget) {
        target = (window.prompt(prompt, state.activeTag) || '').trim();
        if (!target || target === state.activeTag) return;
        const problem = tagProblem(target);
        if (problem) {
            alert(problem);
            return;
        }
    }

    const body = { from: state.activeTag, into: target || null, retire: Boolean(retire) };
    api.json('/api/tags/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    .then(plan => {
        if (plan.error) throw new Error(plan.error);
        const lines = [
            title,
            '',
            `${plan.photos} photo file(s) will be rewritten.`,
        ];
        if (plan.photos_already_carrying_the_target) {
            lines.push(`${plan.photos_already_carrying_the_target} of them already `
                + 'carry the target, and will end up with one tag where they had two.');
        }
        if (plan.embeddings_to_drop) {
            lines.push(`${plan.embeddings_to_drop} cached embedding(s) will be dropped, `
                + 'so it stops being suggested.');
        }
        if (plan.taxonomy_rows_to_drop) {
            lines.push(`${plan.taxonomy_rows_to_drop} taxonomy entr(y/ies) will be removed.`);
        }
        lines.push('', 'This writes to the photo files and cannot be undone.');
        if (!confirm(lines.join('\n'))) return null;

        return api.json('/api/tags/merge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({}, body, { apply: true })),
        });
    })
    .then(result => {
        if (!result) return;
        if (result.error) throw new Error(result.error);
        state.activeTag = target || null;
        loadTags().then(() => {
            if (state.activeTag) showTag(state.activeTag);
            else {
                tagViewName.textContent = 'Select a tag';
                tagViewSummary.textContent = '';
                tagPhotoGrid.innerHTML = '';
                tagViewPlaceholder.classList.remove('hidden');
                setTagActionsEnabled(false);
            }
        });
    })
    .catch(err => {
        console.error(err);
        alert('Could not change that tag: ' + err.message);
    });
}

// Its listeners, which main.js adds once the page has loaded.
export function wireTags() {
    if (btnTagRename) {
        btnTagRename.addEventListener('click', () => changeTag({
            title: `Rename "${state.activeTag}" everywhere it appears?`,
            prompt: 'Rename this tag to:',
            needsTarget: true,
        }));
    }
    if (btnTagMerge) {
        btnTagMerge.addEventListener('click', () => changeTag({
            title: `Merge "${state.activeTag}" into another tag?`,
            prompt: 'Merge into which tag?',
            needsTarget: true,
        }));
    }
    if (btnTagRetire) {
        btnTagRetire.addEventListener('click', () => changeTag({
            title: `Retire "${state.activeTag}"?`,
            needsTarget: false,
            retire: true,
        }));
    }
}
