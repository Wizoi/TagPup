// TagPup's page: the windowed grid -- the one owner of "only the cards on screen exist".
//
// A source of N records (a folder on disk today; a library query in 9b-2) is shown in a CSS
// grid, but only the rows in view and a couple either side are in the DOM. The rows above and
// below are the grid's own padding (--vgrid-before / --vgrid-after, style.css), so the scroll
// bar is right for N cards and the browser lays out a hundred, not N. The columns are the
// stylesheet's (`repeat(auto-fill, ...)`): they are read, never computed here, so a size class,
// a dragged sidebar and the browser's zoom all stay the stylesheet's business.
//
// The source interface (what 9b-2 implements):
//     count()               how many records there are
//     recordAt(i)           the record at index i, 0 <= i < count()
//     indexOfKey(key)       optional: where the record with this key is, or -1 (else a scan)
// A source whose records arrive later (a library view's cards) answers recordAt(i) with a placeholder
// record of the same key and calls patch([key, ...]) when the real one is there.
// and the options:
//     buildCard(record, i)  the card element for a record; an <img data-src="..."> in it is
//                           given its src by the grid, once it has stayed in view a moment
//     cardKey(record)       a string identifying a record across data changes (a path)
//     isBusy(card)          true while something is being typed in the card: it is kept, not
//                           rebuilt, and not taken out of the DOM while it scrolls away
//     afterBuild()          called after a render that built cards
//     afterDraw()           called after every draw of the window (cards built, kept or taken out): the
//                           page's roving tab stop and the focus kept across it (phase 9c)
//     topInset()            pixels at the top of the scroller something sticky covers (a strip above the
//                           grid): scrollToIndex puts a card below it, not under it
//     empty()               the element shown when there is nothing to show
//
// The card that has the keyboard focus is never taken out from under it: where a draw would release it, it is
// kept (laid out of sight, as a card being typed in is) while the focus is in it, and one rebuilt for changed data
// is built again and given the focus again. A page's keys can then count on the focused card being in the DOM.
//
// Layout is measured through one function, `measure({grid, scroller, card})`, so a test can
// give a page numbers (jsdom has no layout) and a hidden grid is told apart from a short one.
// With no layout (jsdom, a hidden tab) the first `fallbackCount` records are drawn, whole.

const FALLBACK_COUNT = 120;
const BUFFER_ROWS = 2;
const IMAGE_ROWS = 1;
// A card is in view for (the view's height + the card's) / the scroll speed: 108 ms when a scroll
// bar is dragged through 760 photos in three seconds (10,400 px/s, measured), so 120 ms lets that
// fly past, and a reader's scroll (a few hundred px/s) asks as soon as the delay is up.
const IMAGE_DELAY_MS = 120;

/**
 * What the browser says of the grid right now, in CSS pixels: the scroller's height and
 * scroll offset, where the grid starts inside the scroller's content, the stylesheet's
 * columns and gaps, and the size of a rendered card (0 when there is none).
 */
export function measureDom({ grid, scroller, card }) {
    const win = grid.ownerDocument.defaultView;
    const styles = win.getComputedStyle(grid);
    const tracks = String(styles.gridTemplateColumns || '').trim();
    const box = card ? card.getBoundingClientRect() : null;
    return {
        viewport: scroller.clientHeight,
        scrollTop: scroller.scrollTop,
        gridTop: grid.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop,
        columns: tracks && tracks !== 'none' ? tracks.split(/\s+/).length : 0,
        cardHeight: box ? box.height : 0,
        cardWidth: box ? box.width : 0,
        rowGap: parseFloat(styles.rowGap) || 0,
        columnGap: parseFloat(styles.columnGap) || 0,
    };
}

export function createVGrid(options) {
    const container = options.container;
    const scroller = options.scroller;
    const win = container.ownerDocument.defaultView;
    const buildCard = options.buildCard;
    const cardKey = options.cardKey || (record => String(record));
    const isBusy = options.isBusy || (() => false);
    const afterBuild = options.afterBuild || (() => {});
    const afterDraw = options.afterDraw || (() => {});
    const topInset = options.topInset || (() => 0);
    const empty = options.empty || null;
    const bufferRows = options.bufferRows ?? BUFFER_ROWS;
    const imageRows = options.imageRows ?? IMAGE_ROWS;
    const imageDelay = options.imageDelay ?? IMAGE_DELAY_MS;
    const fallbackCount = options.fallbackCount ?? FALLBACK_COUNT;
    let source = options.source;
    let measure = options.measure || measureDom;

    // The vgrid's own bookkeeping, not the page's state: which cards are in the DOM.
    const live = new Map();        // key -> { key, card, img, imgState, record, index, generation, since, out }
    const loaded = new Set();      // picture URLs this grid has seen load: the browser has them
    let order = [];                // the entries drawn in the window, in index order
    let generation = 0;            // bumped when the data changed: cards of an older one are rebuilt
    let frame = 0;
    let imageTimer = 0;
    let destroyed = false;
    let layout = null;             // the last measurement with a real card in it
    let stale = true;              // the cards' height must be read again (size, width)
    let anchor = null;             // the row kept in view across a change of layout
    let lastTop = 0;               // the scroll offset to come back to when the grid is shown again
    let laidOut = false;           // the last render had a layout
    let everLaidOut = false;       // the grid has been shown: no layout now means hidden, not unmeasurable
    let measured = false;          // images wait for a moment in view only when there is a layout
    let viewFrom = 0;              // the records in the view, [viewFrom, viewTo), by index
    let viewTo = 0;
    let marginFrom = 0;            // and a row either side of it
    let marginTo = 0;
    let moved = 0;                 // when the view last changed what it showed
    let lastWidth = -1;
    let observer = null;

    function indexOfKey(key) {
        if (source.indexOfKey) return source.indexOfKey(key);
        for (let i = 0, n = source.count(); i < n; i++) {
            if (cardKey(source.recordAt(i)) === key) return i;
        }
        return -1;
    }

    // ---- Pictures -------------------------------------------------------------------
    function startImage(entry, url) {
        entry.imgState = 'loading';
        entry.img.src = url;
    }

    function cancelImage(entry) {
        if (entry.imgState === 'loading') {
            // A picture still being fetched goes with the card: a fast scroll must not
            // leave hundreds of requests behind it.
            entry.img.removeAttribute('src');
            entry.imgState = 'none';
        }
        entry.since = 0;
    }

    function setViewRange(firstSeen, lastSeen, columns, rows) {
        const from = firstSeen * columns;
        const to = (lastSeen + 1) * columns;
        if (from !== viewFrom || to !== viewTo) moved = Date.now();
        viewFrom = from;
        viewTo = to;
        marginFrom = clamp(firstSeen - imageRows, 0, rows - 1) * columns;
        marginTo = (clamp(lastSeen + imageRows, 0, rows - 1) + 1) * columns;
    }

    // Give a picture to each card that has stayed in view for imageDelay ms, and take it from
    // one that left before it came. The row either side of the view is asked for once the view
    // has stood still that long: a scroll that flies past asks for nothing. One timer for all of
    // them, set for the first that is due; `fresh` is a call from a render that just looked.
    function settleImages(fresh) {
        win.clearTimeout(imageTimer);
        imageTimer = 0;
        if (measured && !fresh && layout) {
            // The view may have moved since it was drawn (a frame, at worst): look again.
            const m = measure({ grid: container, scroller, card: firstCard() });
            if (m.viewport > 0) {
                const rows = Math.ceil(source.count() / layout.columns);
                const top = m.scrollTop - m.gridTop;
                const first = clamp(Math.floor(top / layout.stride), 0, rows - 1);
                const last = clamp(Math.floor((top + m.viewport) / layout.stride), first, rows - 1);
                setViewRange(first, last, layout.columns, rows);
            }
        }
        const now = Date.now();
        let next = Infinity;
        for (const entry of live.values()) {
            const img = entry.img;
            if (!img || !img.isConnected) continue;
            const inView = !measured || (!entry.out && entry.index >= viewFrom && entry.index < viewTo);
            const inMargin = !inView && !entry.out && entry.index >= marginFrom && entry.index < marginTo;
            if (!inView && !inMargin) {
                cancelImage(entry);
                continue;
            }
            if (entry.imgState !== 'none') continue;
            const url = img.dataset.src;
            if (!url) continue;
            // A card drawn again by refresh() shows the picture it had, at once: no flash of grey.
            if (!measured || (entry.warm && loaded.has(url))) {
                startImage(entry, url);
                continue;
            }
            if (inView && !entry.since) entry.since = now;
            const due = (inView ? entry.since : moved) + imageDelay;
            if (due <= now) startImage(entry, url);
            else next = Math.min(next, due);
        }
        if (next < Infinity) imageTimer = win.setTimeout(() => settleImages(false), Math.max(0, next - now));
    }

    // ---- Cards ----------------------------------------------------------------------
    function makeEntry(key, record, index) {
        const card = buildCard(record, index);
        const img = card.querySelector('img[data-src]');
        const entry = { key, card, img, imgState: 'none', record, index, generation, since: 0, out: false, warm: false };
        if (img) {
            img.addEventListener('load', () => {
                entry.imgState = 'done';
                if (img.dataset.src) loaded.add(img.dataset.src);
            });
            img.addEventListener('error', () => { entry.imgState = 'error'; });
        }
        return entry;
    }

    function release(entry) {
        cancelImage(entry);
        entry.released = true;
        entry.card.remove();
        live.delete(entry.key);
    }

    // The entry whose card holds the keyboard focus, if any.
    function focusedEntry() {
        const active = container.ownerDocument.activeElement;
        if (!active || active === container.ownerDocument.body || !container.contains(active)) return null;
        for (const entry of live.values()) if (entry.card === active || entry.card.contains(active)) return entry;
        return null;
    }

    function clearStyles(entry) {
        if (!entry.out) return;
        entry.out = false;
        entry.card.style.position = '';
        entry.card.style.top = '';
        entry.card.style.left = '';
        entry.card.style.width = '';
    }

    // A card with an editor open is not taken from under it: out of the window it stays, laid
    // over the place it would have, where nothing can see it and it takes no room.
    function park(entry, m) {
        const cols = m.columns || 1;
        const row = Math.floor(entry.index / cols);
        entry.out = true;
        entry.card.style.position = 'absolute';
        entry.card.style.top = `${row * (m.cardHeight + m.rowGap)}px`;
        entry.card.style.left = `${(entry.index % cols) * ((m.cardWidth || 0) + (m.columnGap || 0))}px`;
        entry.card.style.width = `${m.cardWidth || 0}px`;
    }

    // Make the DOM hold the records [from, to), with `before` and `after` pixels of padding.
    function draw(from, to, before, after, m) {
        container.style.setProperty('--vgrid-before', `${before}px`);
        container.style.setProperty('--vgrid-after', `${after}px`);
        const focused = focusedEntry();
        const next = new Map();
        const drawn = [];
        let built = 0;
        for (let i = from; i < to; i++) {
            const record = source.recordAt(i);
            let key = cardKey(record);
            if (next.has(key)) key = `${key}\u0000${i}`;       // two records of one key: both shown
            let entry = live.get(key);
            let warm = false;
            if (entry && entry.generation !== generation && !isBusy(entry.card)) {
                warm = entry.imgState === 'done';
                release(entry);
                entry = null;
            }
            if (entry) {
                entry.generation = generation;
                entry.record = record;
                entry.index = i;
                clearStyles(entry);
            } else {
                entry = makeEntry(key, record, i);
                entry.warm = warm;
                built++;
            }
            next.set(key, entry);
            drawn.push(entry);
        }
        const above = [];
        const below = [];
        for (const entry of [...live.values()]) {
            if (next.has(entry.key)) continue;
            const index = isBusy(entry.card) || entry === focused ? indexOfKey(entry.key) : -1;
            if (index < 0) {
                release(entry);
                continue;
            }
            entry.index = index;
            entry.record = source.recordAt(index);
            park(entry, m);
            (index < from ? above : below).push(entry);
        }
        // The DOM in index order, moving only what is out of place: a card with an editor in
        // it is never moved, and a moved input loses its focus.
        const sequence = [...above, ...drawn, ...below];
        let cursor = container.firstChild;
        for (const entry of sequence) {
            if (cursor === entry.card) cursor = cursor.nextSibling;
            else container.insertBefore(entry.card, cursor);
        }
        while (cursor) {
            const following = cursor.nextSibling;
            container.removeChild(cursor);
            cursor = following;
        }
        live.clear();
        for (const entry of sequence) live.set(entry.key, entry);
        order = drawn;
        if (built) afterBuild();
        // A card rebuilt under the focus gives it to the one built in its place; a focus with nowhere to go
        // waits on the grid itself, which the page's keys listen on.
        if (focused && focused.released) {
            const fresh = live.get(focused.key);
            const target = fresh && !fresh.card.classList.contains('placeholder') ? fresh.card : container;
            if (target.focus) target.focus({ preventScroll: true });
        }
        afterDraw();
    }

    function showEmpty() {
        for (const entry of [...live.values()]) release(entry);
        order = [];
        container.style.setProperty('--vgrid-before', '0px');
        container.style.setProperty('--vgrid-after', '0px');
        container.style.gridAutoRows = '';
        stale = true;
        laidOut = false;
        if (empty) container.replaceChildren(empty());
        else container.replaceChildren();
    }

    // No layout to go by: the first records, whole, and every picture at once.
    function drawUnmeasured(total) {
        container.style.gridAutoRows = '';
        stale = true;
        laidOut = false;
        measured = false;
        draw(0, Math.min(total, fallbackCount), 0, 0, { columns: 1, cardHeight: 0, rowGap: 0 });
        settleImages(true);
    }

    function firstCard() {
        return order.length ? order[0].card : null;
    }

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value));
    }

    // ---- The window -----------------------------------------------------------------
    function render() {
        if (destroyed) return;
        if (frame) {
            win.cancelAnimationFrame(frame);
            frame = 0;
        }
        const total = source.count();
        if (total === 0) {
            showEmpty();
            return;
        }
        if (container.firstChild && !order.length) container.replaceChildren();
        let m = measure({ grid: container, scroller, card: firstCard() });
        if (!(m.viewport > 0 && m.columns > 0)) {
            if (everLaidOut) {
                // Hidden (a photo is open over it): the window stays as drawn, no card is built
                // and no picture asked for; the next render with a layout redraws and puts the
                // view back where it was.
                laidOut = false;
                return;
            }
            drawUnmeasured(total);
            return;
        }
        let target = null;                         // a scroll offset to go to, once the window is drawn
        if (stale || !(m.cardHeight > 0)) {
            // The height of a card is read from a real one, rows left to their natural height.
            container.style.gridAutoRows = '';
            if (!order.length) {
                draw(0, Math.min(total, m.columns * 2), 0, 0, m);
            }
            m = measure({ grid: container, scroller, card: firstCard() });
            if (!(m.cardHeight > 0)) {
                drawUnmeasured(total);
                return;
            }
            container.style.gridAutoRows = `${m.cardHeight}px`;
            stale = false;
            if (anchor) {
                target = m.gridTop + (Math.floor(anchor.index / m.columns) + anchor.fraction) * (m.cardHeight + m.rowGap);
                anchor = null;
            }
        }
        // Shown again after hidden. The browser forgets a hidden element's scroll offset; when the
        // grid was shown again before any render saw it hidden, the offset is 0 and no scroll
        // event said so (one would have set lastTop to 0).
        if (target === null && lastTop > 0 && (!laidOut || m.scrollTop === 0)) target = lastTop;
        const stride = m.cardHeight + m.rowGap;
        const rows = Math.ceil(total / m.columns);
        const top = (target === null ? m.scrollTop : target) - m.gridTop;
        const firstSeen = clamp(Math.floor(top / stride), 0, rows - 1);
        const lastSeen = clamp(Math.floor((top + m.viewport) / stride), firstSeen, rows - 1);
        const firstRow = clamp(firstSeen - bufferRows, 0, rows - 1);
        const lastRow = clamp(lastSeen + bufferRows, firstRow, rows - 1);
        measured = true;
        everLaidOut = true;
        setViewRange(firstSeen, lastSeen, m.columns, rows);
        layout = { columns: m.columns, stride, gridTop: m.gridTop };
        laidOut = true;
        draw(firstRow * m.columns, Math.min(total, (lastRow + 1) * m.columns),
            firstRow * stride, (rows - 1 - lastRow) * stride, m);
        if (target !== null) {
            scroller.scrollTop = target;
            lastTop = target;
        }
        settleImages(true);
    }

    function schedule() {
        if (frame || destroyed) return;
        frame = win.requestAnimationFrame(() => {
            frame = 0;
            render();
        });
    }

    // The columns or the size of a card changed: read them again, and keep the row that is
    // at the top of the view at the top of the view.
    function relayout() {
        if (layout && laidOut && scroller.clientHeight > 0) {
            const row = (scroller.scrollTop - layout.gridTop) / layout.stride;
            const whole = Math.max(0, Math.floor(row));
            anchor = { index: whole * layout.columns, fraction: Math.max(0, row - whole) };
        }
        stale = true;
        render();
    }

    function onScroll() {
        if (scroller.clientHeight > 0) lastTop = scroller.scrollTop;
        schedule();
    }

    scroller.addEventListener('scroll', onScroll, { passive: true });
    if (typeof win.ResizeObserver === 'function') {
        observer = new win.ResizeObserver(entries => {
            let widthChanged = false;
            for (const entry of entries) {
                if (entry.target !== container) continue;
                const width = entry.contentRect.width;
                if (lastWidth >= 0 && Math.abs(width - lastWidth) > 0.5) widthChanged = true;
                lastWidth = width;
            }
            if (widthChanged) relayout();
            else schedule();
        });
        observer.observe(container);
        observer.observe(scroller);
    } else {
        win.addEventListener('resize', relayout);
    }

    return {
        /** The data changed: draw the window again from it, keeping the scroll position. */
        refresh() {
            generation++;
            render();
        },
        /**
         * These records changed (their cards arrived, or were edited): draw the cards of these keys again from
         * the source, every other card as it is. A card being edited is left alone.
         */
        patch(keys) {
            let any = false;
            for (const key of keys) {
                const entry = live.get(key);
                if (entry && !isBusy(entry.card)) {
                    entry.generation = -1;
                    any = true;
                }
            }
            if (any) render();
        },
        /** A different list altogether (another folder, a filter): from the top. */
        reset() {
            generation++;
            anchor = null;
            lastTop = 0;
            loaded.clear();                 // what the last list loaded is not this list's business
            scroller.scrollTop = 0;
            render();
        },
        /** Draw now what a scroll or a resize has asked for. */
        render,
        /** The cards' size changed (a size class): read it again, keeping the place. */
        relayout,
        setSource(next) {
            source = next;
        },
        setMeasure(next) {
            measure = next || measureDom;
            stale = true;
        },
        indexOfKey,
        /** Bring the record at index i into view; 'start' puts it at the top, else the nearest edge. */
        scrollToIndex(index, align = 'nearest') {
            if (!layout) return false;
            const row = Math.floor(index / layout.columns);
            const inset = Math.max(0, topInset() || 0);
            const topOfRow = layout.gridTop + row * layout.stride - inset;
            const height = scroller.clientHeight;
            let to = scroller.scrollTop;
            if (align === 'start' || topOfRow < to) to = topOfRow;
            else if (topOfRow + layout.stride + inset > to + height) to = topOfRow + layout.stride + inset - height;
            to = Math.max(0, to);
            scroller.scrollTop = to;
            lastTop = to;
            render();
            return true;
        },
        /** How the cards lie now: the columns, and how many rows the view holds (a page of the keys), or null with no layout. */
        geometry() {
            if (!layout || !laidOut) return null;
            return { columns: layout.columns, rows: Math.max(1, Math.floor(scroller.clientHeight / layout.stride)) };
        },
        /** Call fn(card, record, index) for each card in the DOM: marks that follow data. */
        eachCard(fn) {
            for (const entry of live.values()) fn(entry.card, entry.record, entry.index);
        },
        cardFor(key) {
            const entry = live.get(key);
            return entry ? entry.card : null;
        },
        /**
         * What is in the DOM: the window's first and last index (exclusive), and the cards; and `viewFrom`, the
         * first record in the view itself (the window reaches a couple of rows beyond it).
         */
        extent() {
            return {
                first: order.length ? order[0].index : 0,
                end: order.length ? order[order.length - 1].index + 1 : 0,
                cards: live.size,
                viewFrom: Math.min(viewFrom, order.length ? order[order.length - 1].index : 0),
            };
        },
        destroy() {
            destroyed = true;
            win.clearTimeout(imageTimer);
            if (frame) win.cancelAnimationFrame(frame);
            scroller.removeEventListener('scroll', onScroll);
            if (observer) observer.disconnect();
            else win.removeEventListener('resize', relayout);
        },
    };
}
