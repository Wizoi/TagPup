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

/** The tag of a node named `name` under the tag `parentTag` (none: a root): "People/Rowan Thackeray". */
export function joinTag(parentTag, name) {
    return parentTag ? `${parentTag}/${name}` : String(name);
}

// ---- The order tags are shown in ---------------------------------------------------
//
// Every list of tags or people that is shown as a set -- a photo's tags, a picker, an
// autocomplete, a placement question -- is alphabetical, one way: case and accents
// ignored, digits read as numbers ("Trip 3" before "Trip 10"), then the exact spelling,
// so the order is total and a reload does not reshuffle it. A tag path is compared one
// level at a time, the way the tag editor's tree orders it, so "Family" is followed by
// "Family/Amy" and then "Family Tree", not by the tag a space would put first. A list
// that is ranked on purpose (by count, score or similarity) keeps its rank and breaks
// its ties with this order. It is the display's order only: what is written to a file,
// stored or sent keeps the order it has. The server's is tagpup.core.vocabulary.tag_sort_key;
// tests/fixtures/tag_order.json is the table both are held to. The collator is fixed to
// English, not the machine's language, so the order is the same on every computer.
const NAME_ORDER = new Intl.Collator('en', { sensitivity: 'base', numeric: true });

function exactOrder(left, right) {
    return left < right ? -1 : left > right ? 1 : 0;
}

function orderOf(a, b) {
    const shared = Math.min(a.parts.length, b.parts.length);
    for (let i = 0; i < shared; i++) {
        const found = NAME_ORDER.compare(a.parts[i], b.parts[i]);
        if (found !== 0) return found;
    }
    return (a.parts.length - b.parts.length) || exactOrder(a.text, b.text);
}

function ordering(value) {
    const text = String(value ?? '');
    return { text, parts: text.split('/') };
}

/** The order tags are listed in: alphabetical, then the exact spelling, so it is total. */
export function compareTagNames(a, b) {
    return orderOf(ordering(a), ordering(b));
}

/**
 * A copy of `list` in the order tags are shown in. `key` says which text of an item to
 * order by (the item itself when it is a tag). With `rank`, a number each item is
 * ranked by, the biggest first, the alphabet only breaks ties. Each text is taken apart
 * once, not once per comparison.
 */
export function sortedTags(list, key = (item) => item, { rank = null } = {}) {
    const entries = Array.from(list, (item) => ({
        item, ...ordering(key(item)), rank: rank ? Number(rank(item)) || 0 : 0,
    }));
    entries.sort((a, b) => (b.rank - a.rank) || orderOf(a, b));
    return entries.map(entry => entry.item);
}

/** Do these two tags name the same person, however each is spelled? */
export function samePerson(a, b) {
    const left = leafOf(a).toLowerCase();
    return Boolean(left) && left === leafOf(b).toLowerCase();
}

// ---- How a person is labelled -------------------------------------------------------
//
// Where two people share a leaf (two cousins called Sam under Family/Thackeray and Family/Ingersoll), every
// list, chip and hover shows the group too, and only then. The server decides who shares and which tail of
// the path tells them apart (tagpup.core.vocabulary.person_labels, over everyone in the library) and sends each
// person as {id, name, tag, group, shared}; the page only joins them, here, and no page builds the string
// itself or compares two people by it: they compare ids. tests/fixtures/person_labels.json is the table
// this and the server are held to (tests/frontend/person-labels.test.mjs, tests/test_person_labels.py).

/** What stands between a person's name and their group: "Sam · Thackeray". */
export const GROUP_SEPARATOR = ' \u00b7 ';

/** What is shown for a person: their name, and, when another person has it too, the group that tells them apart. */
export function personLabel(person) {
    if (!person) return '';
    const name = String(person.name ?? '');
    return person.shared && person.group ? `${name}${GROUP_SEPARATOR}${person.group}` : name;
}

/** The full tag of a person, for the element's title and the screen reader: where they are filed. */
export function personTitle(person) {
    if (!person) return '';
    return String(person.tag || person.name || '');
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
