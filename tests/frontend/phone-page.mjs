/**
 * The phone page (web/tagpup/mobile.html) as its scenario tests load it: its address, what a test reads of it, the card
 * the library sends, and the layout of a phone, which jsdom does not have. One copy for the phone's scenario files.
 *
 * Every name is made up; the real library is photographs of real people, many of them minors.
 */
import { flush, pageExports } from "./harness.mjs";

/** The phone's page on the library alpha. */
export const PHONE_PAGE = "http://localhost:8090/alpha/mobile.html";

export const el = (ctx, id) => ctx.document.getElementById(id);
/** A tab of the bar at the foot of the page (mobile-tabs.js), by the screen it opens: browse, grid, filter, library. */
export const tab = (ctx, name) => ctx.document.querySelector(`#tabs [data-tab="${name}"]`);
/** The screens shown, by their data-screen. */
export const shown = (ctx) =>
  [...ctx.document.querySelectorAll("[data-screen]")].filter((s) => !s.classList.contains("hidden")).map((s) => s.dataset.screen);
/** The page's own state (mobile-state.js). */
export const pageState = (ctx) => pageExports(ctx.window, "web/tagpup/mobile-state.js").state;
export const wait = (window, ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** The page's timers of `ms` run, and what they started answered. */
export async function settle(ctx, ms = 50) {
  await wait(ctx.window, ms);
  await flush(ctx.window, 6);
}

/**
 * The card the library sends for photo `id` of the folder `meet`: its path, its stamp (`mtime`, also in its thumbnail's
 * address), `media`, as tagpup.services.library_view; `extra` overrides any of them.
 */
export function phoneCard(id, extra = {}, meet = "Rowan's Meet") {
  const mtime = extra.mtime ?? 1718000000.5;
  return {
    id, name: `${meet} ${id}.jpg`, path: `D:\\Photos\\${meet}\\${meet} ${id}.jpg`, taken: "2024:06:01 10:00:00",
    thumb: `/api/photo-thumb?id=${id}&v=${mtime}`, mtime, media: "photo", ...extra,
  };
}

/**
 * The Grid's section given a phone's layout: 3 columns of 120 px cards, 600 px of view; hidden, it has no height and
 * forgets its scroll offset, as the browser does. `here` is the layout, its `scrollTop` the section's.
 */
export function phoneLayout(ctx) {
  const section = ctx.document.querySelector('[data-screen="grid"]');
  const here = { scrollTop: 0, viewport: 600, columns: 3, cardHeight: 130, rowGap: 4, gridTop: 120, cardWidth: 120, columnGap: 4 };
  Object.defineProperty(section, "scrollTop", { get: () => here.scrollTop, set: (v) => { here.scrollTop = v; }, configurable: true });
  Object.defineProperty(section, "clientHeight", { get: () => (section.classList.contains("hidden") ? 0 : here.viewport), configurable: true });
  pageState(ctx).grid.setMeasure(() => {
    if (section.classList.contains("hidden")) {
      here.scrollTop = 0;
      return { ...here, viewport: 0 };
    }
    return { ...here };
  });
  return { here, section };
}
