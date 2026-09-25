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
 * And what may be set as a tag, a name or a caption, which is the server's to say
 * (tagpup/core/validation.py): asked of web/common/validate.js, which applies the
 * rules the server publishes.
 */
import { ruleProblem } from './validate.js';

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
 * The server refuses the same tags, with the same words: both ask the rules of
 * tagpup/core/validation.py, the page through validate.js. Asking here first only
 * means nothing is created for a tag that will be refused, and the text stays where
 * it was typed.
 */
export function tagProblem(tag) {
    return ruleProblem('tag', tag);
}

/** The same for a person's name or one level of a tag, which cannot hold a "/" either. */
export function nameProblem(name) {
    return ruleProblem('name', name);
}

/** The same for a photo's caption, which is its title too. */
export function textProblem(text) {
    return ruleProblem('caption', text);
}
