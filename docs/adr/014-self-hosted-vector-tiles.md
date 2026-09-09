# ADR-014 — The basemap is a file we host, not an API we call

**Status:** Accepted · **Date:** 2026-08-26

## Context

M18 made a map the app's first screen. A map needs a basemap, and every obvious way to get one is a
metered third-party API: Mapbox, MapTiler, Google, Stadia. All of them are keyed, all of them bill
past a free tier, and all of them see every viewport every user pans to.

That runs into three decisions this project has already made.

- **NFR-7** requires the deployment to cost nothing, and says no architectural decision may
  introduce a paid dependency without an ADR.
- **ADR-008** requires any metered dependency to pass through a quota ledger, hard-stop rather than
  auto-bill, and carry no card. A tile API metered per thousand tiles would need all of that
  machinery for a background image.
- **ADR-009** and **ADR-013** both spent real effort keeping user data from leaving. A tile API
  undoes some of that from a different direction: it does not learn who the user is, but it learns
  exactly where they are looking, on every pan, from their browser.

## Decision

**Extract a region of the Protomaps basemap into a single `.pmtiles` archive, serve it as a static
file from the box we already run, and read it in the browser with HTTP range requests.**

No key. No account. No rate limit. No per-tile cost. No third party in the request path at all.

### What is actually built

`scripts/build-basemap.sh` runs `pmtiles extract` against the Protomaps daily planet build. The
extract reads the *remote* archive over range requests, so it downloads the region rather than the
120 GB planet.

Measured, not estimated — greater Chennai, zoom 0–14:

> **6.9 MB, 21 seconds, 42 HTTP requests.**

That number is the whole argument. A basemap small enough to sit on a `Standard_B1s` disk beside the
API, cheap enough that storage and egress round to zero, and quick enough to rebuild that widening
coverage is a one-line command with a different bounding box.

### Where it is served from

Caddy, from the VM's disk, under `/basemap/*` with `file_server` — byte ranges are native, and the
design depends on them: a session pulls a few hundred KB out of a multi-megabyte file.

**Not Blob storage.** ADR-010 disables shared account keys and keeps every container private, and
that is an account-wide guarantee: *"with keys disabled, the only way to reach this data is an Entra
ID identity."* Carving out a public container to hold a public OpenStreetMap derivative would weaken
that sentence for the sake of a file that has no business being near the receipts anyway.

**Not Cloudflare R2**, despite it being the usual recommendation and having free egress: it is a new
third-party account, and R2 signup wants a payment method, which walks straight into ADR-008's "no
card on file".

CORS is load-bearing, exactly as it is for the Blob uploads in ADR-010: the frontend is on Render and
the archive is on the API host, so without the headers the map fails at the preflight and silently
falls back to a plain background.

### Unset is a supported state

`NEXT_PUBLIC_BASEMAP_URL` unset renders every pin over a plain background and says so on screen. The
pins are the product; the basemap is context for them. A deployment that has not built an archive
yet, or one panned outside the covered region, gets a working map rather than a broken one.

### Attribution

The archive is an ODbL Produced Work derived from OpenStreetMap. Attribution is a licence obligation,
so `attributionControl` is passed explicitly rather than left to a default — leaving it `undefined`
rendered no control at all, which was the difference between complying and appearing to.

Self-hosting the basemap is free for any use. The sponsorship Protomaps asks for covers their
*hosted* tile API, which this deliberately does not use.

## Consequences

### Good

- Zero recurring cost, and no possibility of one: there is no meter to run up.
- No third party sees a user's viewport. The strongest privacy property available here is not
  encrypting the request — it is not making it.
- Works offline once cached, which matters for an Android build that is otherwise a WebView over a
  live site.
- Coverage is a build parameter. A city is a bounding box and a re-run.

### Bad, and accepted

- **Coverage is finite and blank outside it.** Chennai only, today. Pan to Delhi and the pins still
  render over nothing. Widening it is cheap; remembering to is a human step.
- **The archive is a deploy artefact nobody versions.** It is gitignored — megabytes of derived data
  do not belong in git — so a fresh environment has a map with no basemap until somebody runs the
  script.
- **It goes stale.** The daily build moves; ours does not until rebuilt. For shop locations that
  matters far less than it would for navigation, but it is real.
- **No labels.** Rendering place names needs a glyph source served from somewhere, which would put
  a second network dependency behind a map whose entire point is having none. The pins carry the
  names that matter. This is the sharpest cost of the decision.
- **MapLibre is pinned to 5.x.** `pmtiles` 4.5 does not work against MapLibre 6: the protocol never
  resolves, the source stays pending, and the map fires neither `load` nor `error` — a working
  archive renders as a blank background with nothing to debug. Found by bisection, and the reason
  the map has an end-to-end test that asserts tiles actually paint rather than that the component
  mounts.

## Alternatives rejected

| Option | Why not |
|---|---|
| Mapbox / MapTiler / Stadia / Google | Metered and keyed. Under ADR-008 each would need a quota ledger, and each sees every viewport. |
| OpenStreetMap's standard raster tiles | No key and free, and its usage policy forbids exactly this: heavy or app use. Fine for a prototype, not for something asking people to install it. |
| Protomaps' hosted tile API | Free for noncommercial use only, and it reintroduces the third party the file removes. |
| A tile server (tileserver-gl, Martin) | A process to run, a database or mbtiles to manage, and memory to spare on a 1 GB box. A static file needs none of them. |
| Ship the archive in `frontend/public/` | Render's build would carry megabytes on every deploy, and the frontend redeploys far more often than the map data changes. **Reversed — see the amendment below.** |

---

## Amendment, 2026-09-09 — the archive ships with the frontend

**Status:** Accepted · Supersedes the last row of *Alternatives rejected*, not the decision itself.

The decision above is unchanged: the basemap is still a file we host and not an API we call, and the
Caddyfile still serves `/basemap/*` with byte ranges. What changed is *which* box is running.

**There is no API host.** The AWS account paused exactly as
[ACCOUNT-MIGRATION.md](../../infra/aws/ACCOUNT-MIGRATION.md) said it would, the EC2 instance is gone,
`terraform.tfstate` holds no resources, and `frugal-api.duckdns.org` does not resolve. The Render
frontend is what is actually serving users. So "copy it to `/opt/frugal/basemap/`" is not a step
anybody can perform, and production had no basemap at all — a coordinate grid and pins, which is the
supported fallback but not the product.

**So the rejected option is taken.** `frontend/public/basemap.pmtiles` is committed, `.gitignore`
carries a single negation for that one path, and `NEXT_PUBLIC_BASEMAP_URL` is `/basemap.pmtiles`.

Two things make this cheaper than the row above assumed:

- **Render honours byte ranges on `public/`** — verified against the live site, `206` with a
  `content-range`. That is the entire requirement; PMTiles fetches a few hundred KB of the archive
  per session exactly as it does from Caddy.
- **Same-origin.** The CORS headers the Caddyfile sets up so carefully are not in play at all, which
  removes the failure mode ADR-014 called load-bearing: an archive that dies at the preflight and
  silently falls back to a plain background.

**The cost is real and accepted.** 7.2 MB enters git history once, and every Render build now carries
it. The original objection — that the frontend redeploys far more often than the map data changes —
stands; it is simply worth less than a production map that works. Rebuilding for a new city is still
`scripts/build-basemap.sh` with a different bbox, now followed by a commit rather than an `scp`.

**When the API host returns**, `/opt/frugal/basemap` is still the better home: it decouples map data
from frontend deploys and is what the Caddyfile is already written for. Moving back is deleting the
negation from `.gitignore`, deleting the file, and setting `NEXT_PUBLIC_BASEMAP_URL` to the absolute
URL again. Nothing in the frontend code distinguishes the two — it reads one variable either way.
