/**
 * Building the page from text, never from markup.
 *
 * What the pages show is the library's: tag names, people's names, captions and
 * paths, most of them written by other programs into the photo files. Put into
 * `innerHTML`, one holding "<" is markup -- an element the page never meant, or a
 * script -- and one holding a quote breaks the attribute it was put in. TagPup's
 * placement question put the typed tag and every tag path it offered into markup
 * that way. So the pages build elements, and text goes in as text:
 *
 *     replaceContent(list, buildElement('span', { className: 'muted', text: 'None' }));
 *     buildElement('label', { className: 'option' }, [
 *         buildElement('input', { attrs: { type: 'radio', name: 'pick', value: tag } }),
 *         buildElement('span', { text: tag }),
 *     ]);
 *
 * tests/frontend/dom-output.test.mjs fails a page module that writes anything but a
 * fixed string into innerHTML, outerHTML or insertAdjacentHTML.
 */

/**
 * A new `tag` element. `options`: `className`, `id`, `text` (its text), `title`,
 * `style` (the style attribute, as written in markup), `attrs` (attributes by name;
 * true is present and empty, false or null absent) and `data` (data- attributes).
 * `children` are elements or strings; a string goes in as text.
 */
export function buildElement(tag, options = {}, children = []) {
    const { className, id, text, title, style, attrs, data } = options;
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (id) node.id = id;
    if (title !== undefined && title !== null) node.title = String(title);
    if (style) node.setAttribute('style', style);
    for (const [name, value] of Object.entries(attrs || {})) {
        if (value === false || value === null || value === undefined) continue;
        node.setAttribute(name, value === true ? '' : String(value));
    }
    for (const [name, value] of Object.entries(data || {})) node.dataset[name] = String(value);
    if (text !== undefined && text !== null) node.textContent = String(text);
    for (const child of [].concat(children)) {
        if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
}

/** Show `children` -- elements or strings, a string as text -- in place of what `container` held. */
export function replaceContent(container, ...children) {
    container.replaceChildren(...children.filter(child => child !== null && child !== undefined && child !== false));
    return container;
}
