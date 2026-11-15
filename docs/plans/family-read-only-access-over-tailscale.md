# Family read-only access over Tailscale: the owner browses and searches the library from a phone, family can look but not change anything

**Status: a plan, nothing built, no decision taken.** Written 2026-11-14 at the owner's request; the owner has not yet
chosen to build it. Live count: not a data feature (no rows written); the surface is 98 routes in the TagPup app and 90 in the
tuner, 41 and 33 of them with a write method, 39 and 28 answering this PC only (counted 2026-11-14 from each app's `url_map`;
a POST is not always a write: searches post).
Domains: Integration (the server, the guards, the routes); Views (what a page lists); Data only for where a person's role is
stored. Integration reviews the seams.

## What Tailscale gives (read in its docs 2026-11-14, not tried here)

- `tailscale serve` forwards a tailnet request to a local port and adds `Tailscale-User-Login` (the sign-in name) and
  `Tailscale-User-Name` to it, replacing any copy the client sent. Requests from a tagged device, or through Funnel, carry
  no identity. The backend must listen on localhost only (it does) or a caller could forge the header.
  https://tailscale.com/kb/1312/serve
- The free Personal plan: six users, unlimited user devices (April 2026 pricing; older pages say 3 users): confirm at
  https://tailscale.com/pricing before relying on it.
- Tailscale access rules choose which devices reach the PC at all; they do not say what a person may do in TagPup.

## Mechanism and its one place

| Mechanism | The ONE place it lives | Owner | New or existing |
|---|---|---|---|
| Who is asking: the login a request arrived with, only when it came from this PC's own address (Serve connects from loopback) | `tagpup/web/security.py` (a new `who()` beside `guard`) | Integration | new |
| Role of a login: owner or viewer; the owner's logins and the viewers' listed in the library's settings, a person unlisted is refused | `tagpup.services.settings` (a journaled change; never `config.ini`) | Data | new setting |
| What a viewer may not do: a route is a `write` or not; a viewer's request to a write route is refused 403 with a sentence | one table in `security.py` next to `THIS_PC_ONLY`, one guard in the `before_request` chain, and `tests/test_every_route_answers_this_pc_or_says_why.py` extended so a route in neither list fails the build | Integration | new, beside the existing one |
| The Host check lets the tailnet's host name through | `security.LOCAL_HOSTS` | Integration | existing, widened by the name the owner gives |

A route whose method is POST but only reads (search, a plan) is `read`, listed by name. A route nobody classified is refused
to a viewer: the safe default is "write".

## Decided here, for the owner to confirm

- The owner on this PC (no login header, loopback) keeps today's behaviour exactly. Nothing changes for the apps as run now.
- A phone through Serve has the header: the owner's own login is the `owner` role (full access, from a phone too); a family
  login is `viewer`; any other login or none is refused.
- Not on: Funnel (public). Not built: passwords, sessions, accounts, HTTPS of our own (Tailscale encrypts the link).
- The Activity page, the roots, drains and Name faces stay this PC only for everyone (they already are).

## Failure modes the build must answer

- A request that reaches the app on another address with a forged `Tailscale-User-Login`: the login is read only when the
  connection's address is loopback; any other address is refused as today.
- The owner removes a viewer: the next request is refused, no session to expire.
- A viewer opens an old page and presses Save: 403 with a sentence, nothing written, nothing journaled.
- A new route added later and not classified: the route-classification test fails the build.
- Two logins that spell one person differently: compared case-folded, and one place does the comparing.

## Steady-state writer, one-offs

No data is written by the feature, so no steady-state writer and no one-off. A migration is not needed (the role list is a
library setting).

## Cuts

1. Integration: `who()`, the role guard, the write/read table for all the routes, the route test. About 400 lines, most of it
   the table. The apps behave as today when no header arrives.
2. Data: the setting that lists the owner's and the viewers' logins, through its journaled change, with the Settings page
   field for it (about 150 lines).
3. The owner: install Tailscale on the PC and the phone, `tailscale serve --bg 8080`, add the family. Nothing to merge.

Each cut stays under the 1,200-line budget. A phone's layout is not part of this plan: whether the pages are usable at phone
width is a separate question, to be looked at in a real browser first.

## Open questions for the owner

1. Who gets viewer access, and does a viewer need Identify Faces, search and downloads of full pictures (all reads), or only
   the grid?
2. Is the owner's own phone to have full write access, or read-only too (safer on a lost phone)?
3. Is a viewer to see every folder, or only some? (This plan gives everything; folder-level limits would be a larger design.)
