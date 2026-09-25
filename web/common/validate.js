/**
 * What may be set, asked in the page before anything is sent.
 *
 * The rules are the server's (tagpup/core/validation.py), which every service checks
 * what it is given against before it writes. The pages kept copies of four of them,
 * and the library-name rule was a pattern the page repeated without the names the
 * server reserves. Now the server publishes the rules as data -- /api/rules: each kind
 * of input, its patterns, forbidden text, lengths, ranges and choices, and the message
 * each gives -- and this fetches them once and applies them, so no page keeps a copy.
 *
 * What is code here is the checks themselves, each the twin of one in validation.py
 * under the same name. tests/validation_cases.json runs through both
 * (tests/test_validation.py, tests/frontend/validation.test.mjs), so the two cannot
 * give different answers.
 *
 * Until the rules have arrived nothing is refused here: the server still is, in the
 * same words, and the page shows its refusal as it would any other.
 */
import { api } from './api.js';

let published = null;
let loading = null;

/** Fetch the rules, once; later calls share the first. Resolves the rules, or null. */
export function loadRules() {
    if (!loading) {
        loading = api.json('/api/rules')
            .then(data => {
                if (data && data.kinds) {
                    published = data;
                } else {
                    loading = null;
                }
                return published;
            })
            .catch(err => {
                console.error('Could not load the rules of what may be set:', err);
                loading = null;
                return null;
            });
    }
    return loading;
}

/** Use these rules (what /api/rules answered) without fetching them: for the tests. */
export function useRules(rules) {
    published = rules;
    loading = Promise.resolve(rules);
}

/** The version of the rules in use, or null before they arrive. */
export function rulesVersion() {
    return published ? published.version : null;
}

// ---- The checks: each the twin of validation.py's CHECKS entry of the same name ------------

/** A value as text: null is empty; true and false as they are spelled. */
function asText(value) {
    return value === null || value === undefined ? '' : String(value);
}

function isBlank(value) {
    return !Array.isArray(value) && !asText(value).trim();
}

const INTEGER = /^[+-]?[0-9]+$/;
const NUMBER = /^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/;
const BOOLEANS = new Set(['true', 'false', 'yes', 'no', 'on', 'off', '1', '0']);

/** The value as a number, or null when it is not one. */
function asNumber(value) {
    if (typeof value === 'boolean') return null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    const text = asText(value).trim();
    return NUMBER.test(text) ? parseFloat(text) : null;
}

/**
 * How many bytes `text` takes in UTF-8. A lone surrogate counts three, as the
 * replacement character it is written as, and as validation.py counts it.
 */
function utf8Length(text) {
    let bytes = 0;
    for (let i = 0; i < text.length; i++) {
        const code = text.charCodeAt(i);
        if (code < 0x80) {
            bytes += 1;
        } else if (code < 0x800) {
            bytes += 2;
        } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length
                   && (text.charCodeAt(i + 1) & 0xfc00) === 0xdc00) {
            bytes += 4;
            i++;
        } else {
            bytes += 3;
        }
    }
    return bytes;
}

const compiled = new Map();

/** A pattern's RegExp, made once: `whole` matches the whole value, else anywhere in it. */
function regex(pattern, whole) {
    const key = (whole ? 'whole:' : 'any:') + pattern;
    if (!compiled.has(key)) compiled.set(key, new RegExp(whole ? `^(?:${pattern})$` : pattern));
    return compiled.get(key);
}

const CHECKS = {
    required: (rule, value) => isBlank(value),
    forbid_pattern: (rule, value) => regex(rule.pattern, false).test(asText(value)),
    forbid_text: (rule, value) => asText(value).includes(rule.text),
    pattern: (rule, value) => !regex(rule.pattern, true).test(asText(value)),
    must_contain: (rule, value) => !asText(value).includes(rule.text),
    reserved: (rule, value) => rule.names.includes(asText(value).trim().toLowerCase()),
    max_bytes: (rule, value) => utf8Length(asText(value)) > rule.max,
    integer: (rule, value) => {
        if (typeof value === 'boolean') return true;
        if (typeof value === 'number') return !Number.isFinite(value) || !Number.isInteger(value);
        return !INTEGER.test(asText(value).trim());
    },
    number: (rule, value) => asNumber(value) === null,
    boolean: (rule, value) => typeof value !== 'boolean' && !BOOLEANS.has(asText(value).trim().toLowerCase()),
    range: (rule, value) => {
        const number = asNumber(value);
        return number === null || !(rule.min <= number && number <= rule.max);
    },
};

function items(rule, value) {
    const listed = Array.isArray(value)
        ? value.map(item => asText(item).trim())
        : asText(value).split(rule.separator).map(item => item.trim());
    return rule.skip_empty ? listed.filter(Boolean) : listed;
}

function check(rules, value) {
    for (const rule of rules) {
        if (rule.rule === 'optional') {
            if (isBlank(value)) return null;
            continue;
        }
        if (rule.rule === 'list') {
            const listed = items(rule, value);
            if ('count' in rule && listed.length !== rule.count) return rule.message;
            for (const item of listed) {
                const found = check(rule.each, item);
                if (found) return found;
            }
            continue;
        }
        const fails = CHECKS[rule.rule];
        if (!fails) throw new Error(`validate.js has no check called ${rule.rule}`);
        if (fails(rule, value)) return rule.message.split('{value}').join(asText(value).trim());
    }
    return null;
}

/**
 * Why `value` cannot be set as a `kind` ("tag", "name", "caption", "library name",
 * "grouping", "folder", "time shift", "setting faces.min_face_size", ...), or null if
 * it can -- or if the rules have not arrived yet.
 */
export function ruleProblem(kind, value) {
    if (!published) return null;
    const declared = published.kinds[kind];
    if (!declared) throw new Error(`No rules for ${kind}`);
    return check(declared.rules, value);
}
