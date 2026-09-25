/**
 * How the pages talk about a tag: the same helpers in both, written once.
 *
 * A person has two shapes and they are not interchangeable. Their identity is a leaf
 * -- "Hazel Brookmire" -- which is what the faces table, the suggester and
 * photo.people speak in. Their tag is a path -- "People/Hazel Brookmire" -- which is
 * what the keywords must hold and what the server matches on, exactly. Every bug in
 * this area was a site converting between the two by hand, so the conversions live
 * here: tests/frontend/tag-vocabulary.test.mjs fails on a raw `.split('/')` in the
 * pages outside them. The server's reading of a tag is tagpup/core/vocabulary.py's.
 *
 * And what may be set as a tag or a name, in the server's words:
 * tests/frontend/tag-rules.test.mjs holds these to tests/tag_rules.json, as the
 * server's problem_with_tag is held.
 */

/** The last segment of a tag path: the name a person is known by. */
export function leafOf(tag) {
    if (!tag) return '';
    const text = String(tag);
    return text.includes('/') ? text.split('/').pop().trim() : text.trim();
}

/** The first segment of a tag path: the category it is filed under. */
export function rootOf(tag) {
    if (!tag) return '';
    return String(tag).split('/')[0].trim();
}

/** Do these two tags name the same person, however each is spelled? */
export function samePerson(a, b) {
    const left = leafOf(a).toLowerCase();
    return Boolean(left) && left === leafOf(b).toLowerCase();
}

/**
 * Does this photo already carry this tag, or this same person under another name?
 *
 * A plain `tags.includes()` compared "Hazel Brookmire" against
 * "People/Hazel Brookmire", found no match, and wrote the person in a second
 * time. Identity is the leaf; the path is only where they are filed.
 *
 * `isPersonTag` is the page's own answer to whether a tag names a person: which
 * roots hold people is the tag tree's, and only the page has the tree loaded.
 */
export function photoAlreadyHas(photo, tag, isPersonTag) {
    const tags = (photo && photo.tags) || [];
    if (tags.includes(tag)) return true;
    if (!isPersonTag(tag)) return false;
    return tags.some(t => isPersonTag(t) && samePerson(t, tag));
}

/**
 * Why this cannot be set as a tag, or null if it can.
 *
 * The server refuses the same tags (problem_with_tag, tagpup/core/vocabulary.py),
 * with the same words; tests/tag_rules.json holds both to one list. Asking here
 * first only means nothing is created for a tag that will be refused, and the
 * text stays where it was typed.
 */
export function tagProblem(tag) {
    return textProblem(tag, 'A tag', true);
}

/** The same for a person's name or one level of a tag, which cannot hold a "/" either. */
export function nameProblem(name) {
    return textProblem(name, 'A name', false);
}

export function textProblem(value, what, levels) {
    // Controls first, as the server asks: the two languages disagree on whether
    // some of them count as space.
    const text = value == null ? '' : String(value);
    if (/[\u0000-\u001F\u007F-\u009F\u2028\u2029\uFEFF]/.test(text)) {
        return `${what} cannot contain a tab, a line break or another control character.`;
    }
    for (const mark of ['|', '\\']) {
        if (text.includes(mark)) {
            return `${what} cannot contain "${mark}": other programs read it as a break between levels.`
                + (levels ? ' Use "/" instead.' : '');
        }
    }
    if (!text.trim()) return `${what} cannot be empty.`;
    if (!levels && text.includes('/')) {
        return 'A name cannot contain "/": it separates the levels of a tag.';
    }
    if (levels && /(?:^|\/)\s*(?:\/|$)/.test(text)) {
        return `${what} cannot have an empty level, as in "A//B" or "A/".`;
    }
    return null;
}
