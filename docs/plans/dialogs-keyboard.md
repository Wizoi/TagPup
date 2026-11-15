# Dialogs keep the keyboard: every popup cancels on Esc, tabs through its buttons, and gives the focus back
Live count: about 12 dialogs on the two pages (TagPup 2 in index.html and 6 built in JS; TagTuner 5 in index.html and 3 in JS), counted by reading the pages 2026-11-15; each handles Esc and the focus its own way or not at all.
Domains: Views (both pages' front end only; no server, no data, no route); integration reviews the seam between the two pages' shared `web/common/`.

| Mechanism | The ONE place it lives (module, function) | Its owner | New or existing |
| Is a dialog open (the pages' shortcuts ask) | `web/common/dialog.js` `dialogOpen` | Views | existing: reuse |
| Esc cancels, Tab wraps inside, focus is remembered on open and returned on close | `web/common/dialog.js`, one helper every dialog calls; the hand-rolled Esc handlers move onto it (a deletion) | Views | new helper, replaces about 8 copies |
| The focused button looks like the Enter default; the primary looks like it when no other button has focus | the shared dialog CSS (`web/common/`), not per dialog | Views | existing CSS, extended |
| Tab order of the main pages | each page's own markup; fixes in place | Views | existing |

Steady-state writer: a dialog added afterwards uses the helper and the shared classes; `tests/frontend/dialogs-keep-the-keys.test.mjs` opens each one and fails for one that does not.
One-offs: none.
Cuts: one branch (about 1,200 lines or fewer), the helper and the dialogs on the TagPup page first, then the TagTuner page's if over budget.
