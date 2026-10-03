// TagPup's page: what the navigator shows, as data (docs/ARCHITECTURE.md, phase 9c). No page, no request: the
// counts GET /api/library/navigator answers, made into the rows a tree shows -- which are the library's folders,
// the tag tree, the people and the years -- and the rows' ids, so that the source a view was opened from
// can be found in them again. navigator.js asks, paints and listens; this is what can be tested and measured alone.
//
// A row is { id, level, label, count, hint, title, expandable, expanded, spec } where `spec` is what
// openLibraryView takes ({ kind, value, recursive }) or null for a row that only groups (the junk years).
// The lists are the whole library's -- 2,746 folders, 895 keyword nodes, 413 people, 61 years on photo_index -- and
// a tree shows only what is expanded, at most NAV_MAX_ROWS rows: the page never draws them all at once.
import { baseName, pathKey } from './common/paths.js';
import { compareTagNames } from './common/vocabulary.js';

/** The most rows one section draws; the rest are said in a line and reached by the filter. */
export const NAV_MAX_ROWS = 400;

/** Years outside this range (to the current year + 1) are grouped as 'Other years': scanned dates gone wrong. */
export const NAV_FIRST_YEAR = 1970;

const NAV_MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
    'October', 'November', 'December'];

function navPlural(count, one, many) {
    return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

// ---- Folders ---------------------------------------------------------------------------------

/**
 * The folder tree of the route's flat list, [{ path, name, parent, direct, recursive }]: nodes by the key of
 * their path (pathKey: the same folder in any spelling), each holding its children in the list's order. A folder
 * whose parent is not in the list is a top of the tree.
 */
export function indexFolders(list) {
    const byKey = new Map();
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.path !== 'string') continue;
        const key = pathKey(each.path);
        byKey.set(key, {
            id: `f:${key}`, key, path: each.path, name: each.name || baseName(each.path) || each.path,
            parentKey: each.parent ? pathKey(each.parent) : null,
            direct: Number(each.direct) || 0, recursive: Number(each.recursive) || 0, children: [],
        });
    }
    const tops = [];
    for (const node of byKey.values()) {
        const parent = node.parentKey !== null ? byKey.get(node.parentKey) : null;
        if (parent && parent !== node) parent.children.push(node);
        else tops.push(node);
    }
    return { byKey, tops, size: byKey.size };
}

function navFolderRow(node, level, expanded) {
    const open = expanded.has(node.id);
    return {
        id: node.id, level, label: node.name, count: node.recursive,
        title: `${node.path}\n${navPlural(node.direct, 'photo', 'photos')} in it, ${navPlural(node.recursive, 'photo', 'photos')} with its subfolders`,
        aria: `${node.name}, ${navPlural(node.recursive, 'photo', 'photos')} with its subfolders`,
        hint: '', expandable: node.children.length > 0, expanded: open && node.children.length > 0,
        spec: { kind: 'folder', value: node.path, recursive: true },
    };
}

// ---- Keywords --------------------------------------------------------------------------------

/**
 * The tag tree of the route's list, [{ tag, name, parent, count }] (`parent` the tag of the node above, or null):
 * nodes by tag and by the tag without case, each holding its children alphabetically (compareTagNames).
 */
export function indexKeywords(list) {
    const byTag = new Map();
    const byLower = new Map();
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.tag !== 'string') continue;
        const node = {
            id: `k:${each.tag}`, tag: each.tag, name: each.name || each.tag, parentTag: each.parent || null,
            count: Number(each.count) || 0, children: [],
        };
        byTag.set(node.tag, node);
        if (!byLower.has(node.tag.toLowerCase())) byLower.set(node.tag.toLowerCase(), node);
    }
    const tops = [];
    for (const node of byTag.values()) {
        const parent = node.parentTag !== null ? byTag.get(node.parentTag) : null;
        if (parent && parent !== node) parent.children.push(node);
        else tops.push(node);
    }
    const alphabetical = (a, b) => compareTagNames(a.name, b.name) || compareTagNames(a.tag, b.tag);
    tops.sort(alphabetical);
    for (const node of byTag.values()) node.children.sort(alphabetical);
    return { byTag, byLower, tops, size: byTag.size };
}

function navKeywordRow(node, level, expanded) {
    const open = expanded.has(node.id);
    return {
        id: node.id, level, label: node.name, count: node.count,
        title: `${node.tag}\n${navPlural(node.count, 'photo', 'photos')} with it or a keyword under it`,
        aria: `${node.name}, ${navPlural(node.count, 'photo', 'photos')}`,
        hint: '', expandable: node.children.length > 0, expanded: open && node.children.length > 0,
        spec: { kind: 'keyword', value: node.tag, recursive: false },
    };
}

// ---- People ----------------------------------------------------------------------------------

/** The people of the route's list, [{ name, count }], alphabetically (names differing in case are one: the route merges them). */
export function indexPeople(list) {
    const people = [];
    const byLower = new Map();
    for (const each of Array.isArray(list) ? list : []) {
        if (!each || typeof each.name !== 'string') continue;
        people.push({ id: `p:${each.name}`, name: each.name, count: Number(each.count) || 0 });
    }
    people.sort((a, b) => compareTagNames(a.name, b.name));
    for (const person of people) if (!byLower.has(person.name.toLowerCase())) byLower.set(person.name.toLowerCase(), person);
    return { people, byLower, size: people.length };
}

function navPersonRow(person) {
    return {
        id: person.id, level: 1, label: person.name, count: person.count,
        title: `${person.name}\n${navPlural(person.count, 'photo', 'photos')}`,
        aria: `${person.name}, ${navPlural(person.count, 'photo', 'photos')}`,
        hint: '', expandable: false, expanded: false,
        spec: { kind: 'person', value: person.name, recursive: false },
    };
}

// ---- Dates -----------------------------------------------------------------------------------

/**
 * The years of the route's { years: [{ year, count, months: [{ month, count }], other }], undated }: newest
 * first, and those outside NAV_FIRST_YEAR .. `thisYear` + 1 apart, as 'Other years' (a date a camera's clock got
 * wrong, or a scan's): still reachable, collapsed, at the end.
 */
export function indexDates(data, thisYear = new Date().getFullYear()) {
    const years = [];
    for (const each of data && Array.isArray(data.years) ? data.years : []) {
        if (!each || !Number.isFinite(each.year)) continue;
        const months = (Array.isArray(each.months) ? each.months : [])
            .filter(m => m && typeof m.month === 'string' && /^\d{4}-\d{2}$/.test(m.month))
            .map(m => ({ month: m.month, number: Number(m.month.slice(5)), count: Number(m.count) || 0 }))
            .sort((a, b) => a.number - b.number);
        years.push({ year: each.year, count: Number(each.count) || 0, other: Number(each.other) || 0, months });
    }
    years.sort((a, b) => b.year - a.year);
    const usual = years.filter(each => each.year >= NAV_FIRST_YEAR && each.year <= thisYear + 1);
    const odd = years.filter(each => !usual.includes(each));
    const byYear = new Map(years.map(each => [each.year, each]));
    return {
        usual, odd, byYear, size: years.length,
        oddPhotos: odd.reduce((sum, each) => sum + each.count, 0),
        undated: data && Number.isFinite(data.undated) ? data.undated : 0,
    };
}

export const OTHER_YEARS_ID = 'y:other';

function navYearRow(each, level, expanded) {
    const expandable = each.months.length > 0;
    return {
        id: `y:${each.year}`, level, label: String(each.year), count: each.count,
        title: `${navPlural(each.count, 'photo', 'photos')} taken in ${each.year}`,
        aria: `${each.year}, ${navPlural(each.count, 'photo', 'photos')}`,
        hint: '', expandable, expanded: expandable && expanded.has(`y:${each.year}`),
        spec: { kind: 'year', value: String(each.year), recursive: false },
    };
}

function navMonthRows(each, level) {
    const rows = each.months.map(m => ({
        id: `m:${m.month}`, level, label: `${NAV_MONTH_NAMES[m.number - 1] || m.month}`, count: m.count,
        title: `${navPlural(m.count, 'photo', 'photos')} taken in ${NAV_MONTH_NAMES[m.number - 1] || m.month} ${each.year}`,
        aria: `${NAV_MONTH_NAMES[m.number - 1] || m.month} ${each.year}, ${navPlural(m.count, 'photo', 'photos')}`,
        hint: '', expandable: false, expanded: false,
        spec: { kind: 'month', value: m.month, recursive: false },
    }));
    if (each.other > 0) {
        // Photos of the year whose date names no month of it: counted, and reached by the year.
        rows.push({
            id: `o:${each.year}`, level, label: 'Other', count: each.other,
            title: `${navPlural(each.other, 'photo', 'photos')} of ${each.year} with no month. Open the year to see them.`,
            aria: `Other, ${navPlural(each.other, 'photo', 'photos')} of ${each.year} with no month`,
            hint: '', expandable: false, expanded: false, spec: { kind: 'year', value: String(each.year), recursive: false },
        });
    }
    return rows;
}

// ---- Rows ------------------------------------------------------------------------------------

function navCapped(rows, cap, total) {
    if (rows.length <= cap) return { rows, hidden: 0 };
    return { rows: rows.slice(0, cap), hidden: Math.max(total, rows.length) - cap };
}

function navMatches(text, needle) {
    return String(text).toLowerCase().includes(needle);
}

function navWalk(nodes, level, expanded, make, out, cap) {
    for (const node of nodes) {
        if (out.length > cap) return;
        const row = make(node, level, expanded);
        out.push(row);
        if (row.expanded) navWalk(node.children, level + 1, expanded, make, out, cap);
    }
}

/**
 * The rows of a section for what is expanded and what is typed in its filter: { rows, hidden } -- `hidden` rows
 * beyond NAV_MAX_ROWS are not drawn. With a filter the tree is a flat list of what navMatches (a folder by its name or
 * its path, a keyword by its tag, a person or a date by its words), each with the place it is filed in.
 */
export function sectionRows(section, index, expanded, filter, cap = NAV_MAX_ROWS) {
    if (!index) return { rows: [], hidden: 0 };
    const needle = String(filter || '').trim().toLowerCase();
    const out = [];
    if (section === 'people') {
        for (const person of index.people) {
            if (!needle || navMatches(person.name, needle)) out.push(navPersonRow(person));
        }
        return navCapped(out, cap, out.length);
    }
    if (section === 'dates') return navDateRows(index, expanded, needle, cap);
    const make = section === 'folders' ? navFolderRow : navKeywordRow;
    if (!needle) {
        navWalk(index.tops, 1, expanded, make, out, cap);
        return navCapped(out, cap, out.length);
    }
    let found = 0;
    const nodes = section === 'folders' ? index.byKey.values() : index.byTag.values();
    const all = [];
    for (const node of nodes) {
        const where = section === 'folders' ? node.path : node.tag;
        if (!navMatches(node.name, needle) && !navMatches(where, needle)) continue;
        found += 1;
        if (all.length <= cap) all.push(node);
    }
    if (section === 'keywords') all.sort((a, b) => compareTagNames(a.tag, b.tag));
    for (const node of all) {
        const row = make(node, 1, new Set());
        row.expandable = false;
        row.expanded = false;
        row.hint = section === 'folders' ? node.path : node.tag;
        out.push(row);
    }
    return navCapped(out, cap, found);
}

function navDateRows(index, expanded, needle, cap) {
    const out = [];
    const everyYear = [...index.usual, ...index.odd];
    if (needle) {
        let found = 0;
        for (const each of everyYear) {
            if (navMatches(each.year, needle)) {
                found += 1;
                if (out.length <= cap) { const row = navYearRow(each, 1, new Set()); row.expandable = false; out.push(row); }
            }
            for (const m of each.months) {
                const words = `${NAV_MONTH_NAMES[m.number - 1] || ''} ${each.year} ${m.month}`;
                if (!navMatches(words, needle)) continue;
                found += 1;
                if (out.length <= cap) {
                    const row = navMonthRows({ ...each, months: [m], other: 0 }, 1)[0];
                    row.hint = String(each.year);
                    out.push(row);
                }
            }
        }
        return navCapped(out, cap, found);
    }
    for (const each of index.usual) {
        const row = navYearRow(each, 1, expanded);
        out.push(row);
        if (row.expanded) out.push(...navMonthRows(each, 2));
    }
    if (index.odd.length) {
        const open = expanded.has(OTHER_YEARS_ID);
        out.push({
            id: OTHER_YEARS_ID, level: 1, label: `Other years (${index.odd.length.toLocaleString()})`, count: index.oddPhotos,
            title: `Years before ${NAV_FIRST_YEAR} or after next year, from dates that are probably wrong`,
            aria: `Other years, ${navPlural(index.odd.length, 'year', 'years')}, ${navPlural(index.oddPhotos, 'photo', 'photos')}`,
            hint: '', expandable: true, expanded: open, spec: null,
        });
        if (open) {
            for (const each of index.odd) {
                const row = navYearRow(each, 2, expanded);
                out.push(row);
                if (row.expanded) out.push(...navMonthRows(each, 3));
            }
        }
    }
    return navCapped(out, cap, out.length);
}

// ---- Finding a view's source in the tree -----------------------------------------------------

/**
 * The row id a view's source is, and the ids of the rows to open so that it shows: { id, open: [ids] } or null
 * when the tree has no row for it (a keyword with no node, a person no photo names, a folder the library holds
 * no photo in).
 */
export function locate(section, index, spec) {
    if (!index || !spec) return null;
    if (section === 'folders' && spec.kind === 'folder') {
        let node = index.byKey.get(pathKey(spec.value));
        if (!node) return null;
        const id = node.id;
        const open = [];
        while (node && node.parentKey !== null) {
            node = index.byKey.get(node.parentKey);
            if (node) open.push(node.id);
        }
        return { id, open };
    }
    if (section === 'keywords' && spec.kind === 'keyword') {
        let node = index.byTag.get(spec.value) || index.byLower.get(String(spec.value).toLowerCase());
        if (!node) return null;
        const id = node.id;
        const open = [];
        const seen = new Set();
        while (node && node.parentTag !== null && !seen.has(node.tag)) {
            seen.add(node.tag);
            node = index.byTag.get(node.parentTag);
            if (node) open.push(node.id);
        }
        return { id, open };
    }
    if (section === 'people' && spec.kind === 'person') {
        const person = index.byLower.get(String(spec.value).toLowerCase());
        return person ? { id: person.id, open: [] } : null;
    }
    if (section === 'dates' && (spec.kind === 'year' || spec.kind === 'month')) {
        const year = spec.kind === 'year' ? Number(spec.value) : Number(String(spec.value).slice(0, 4));
        const each = index.byYear.get(year);
        if (!each) return null;
        const open = [];
        if (index.odd.includes(each)) open.push(OTHER_YEARS_ID);
        if (spec.kind === 'month') {
            if (!each.months.some(m => m.month === spec.value)) return null;
            open.push(`y:${year}`);
            return { id: `m:${spec.value}`, open };
        }
        return { id: `y:${year}`, open };
    }
    return null;
}

/** Which tab (section) a source belongs to; null for the whole library, which no tab names. */
export function sectionOf(spec) {
    if (!spec) return null;
    if (spec.kind === 'folder') return 'folders';
    if (spec.kind === 'keyword') return 'keywords';
    if (spec.kind === 'person') return 'people';
    if (spec.kind === 'year' || spec.kind === 'month') return 'dates';
    return null;
}
