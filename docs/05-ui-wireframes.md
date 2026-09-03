# Frugal — UI Design & Wireframes

**Version:** 1.0 · **Last updated:** 2026-08-04
**Companion documents:** [SRS](01-srs.md) · [API design](04-api-design.md) · [Project structure](06-project-structure.md)

---

## 1. Design principles

| Principle | What it means here |
|---|---|
| **Explanation is always one click away** | Every score, verdict, and forecast renders through the same `<ExplanationPanel>`. No number appears without a path to its factors. |
| **Honest empty and partial states** | "Not enough data yet" is a designed state with a call to action, never a zero or a blank chart. |
| **Progressive disclosure** | Headline first, factors on demand, raw data at the bottom. A dashboard that opens with a factor table is unreadable. |
| **Decisions, not dashboards** | The primary CTA on every screen moves the user toward an action, not toward another chart. |
| **Confidence is visible** | Where an engine is unsure, the UI says so at the same visual weight as the number itself. |

---

## 2. Design system

### 2.1 Foundations

Frugal's data and status palettes are the **validated reference palette** rather than an invented one —
an unvalidated palette would have to clear the same colorblind-safety gates anyway, and inventing colors
first is the most common way charts end up unreadable.

A **brand** reservation was added on top of that in the art-direction pass, and it is chrome only: mark,
primary identity action, links, active nav, focus ring. It never encodes a value and never becomes a
series. Its hue was forced — blue, orange, aqua, yellow, green, red and salmon are all spoken for, which
leaves violet — but hue alone was not enough to keep it safe. Measured under Machado-Oliveira against
all eight palette colors, a violet at the chart slots' own lightness collapses onto `--series-1`
(worst-case ΔE 4.4 and 5.4, under the floor of 6). So the brand separates by **lightness**, living at the
ends of its ramp where the chart palette has nothing: `#4c1e73` on light (ΔE 31.6) and `#c39dee` on dark
(ΔE 17.2). Same reservation logic the status colors already use.

**Typeface:** three faces, each with one job. **Instrument Sans** carries the interface — it ships `tnum`,
which the `.tabular` rule depends on; a face without it makes that rule a silent no-op and money columns
stop aligning with no error anywhere. **IBM Plex Mono** carries figures that must align. **Instrument
Serif** carries marketing headings and the cold-start welcome, through the `type-editorial` roles — it is
deliberately not folded into `type-hero`/`type-display`, because both of those are used on figures as
well as headings and the serif has no `tnum`.
`font-variant-numeric: tabular-nums` on table columns and axis ticks only — anywhere digits must align
vertically. Standalone figures use proportional figures.

**Spacing:** 4px base scale (4/8/12/16/24/32/48/64). Sections are separated by 32px, content within a
section by 16px, and every grid gutter is 16px. Container padding has exactly two values — **24px**
for a primary block and **16px** for a dense one (tiles, list rows, feed items). Nothing uses 20px.

**Radius:** four values, exposed as Tailwind utilities from the `--radius-*` theme namespace, never as
arbitrary `rounded-[…]`:

| Token | Utility | Value | Used for |
|---|---|---|---|
| `--radius-swatch` | `rounded-swatch` | 2px | Legend and category chips |
| `--radius-inner` | `rounded-inner` | 4px | Concentric nesting — a 6px container with 2px padding |
| `--radius-control` | `rounded-control` | 6px | Buttons, inputs, selects, notice strips |
| `--radius-card` | `rounded-card` | 10px | Cards, charts, bordered sections |

**Type scale.** Six named roles, defined as Tailwind `@utility` classes so that size, weight, tracking
and leading always travel together. They are `type-*` rather than `text-*` deliberately: tailwind-merge
classifies an unrecognised `text-foo` as a text *colour*, so `cn("text-title", "text-ink-muted")` would
silently drop the size.

| Utility | Size / leading / tracking | Role |
|---|---|---|
| `type-hero` | 48px / 1.08 / −0.035em / 600 | Public home screen headline only — never in the application |
| `type-display` | 32px / 1.15 / −0.03em / 600 | Largest routine heading — onboarding, hero figures |
| `type-title` | 24px / 1.25 / −0.02em / 600 | Page `h1`, stat-tile values |
| `type-section` | 16px / 1.4 / −0.01em / 500 | Section and card headings |
| `type-body` | 14px / 1.55 | Body copy, table cells, controls |
| `type-meta` | 12px / 1.45 | Captions, hints, timestamps |
| `type-eyebrow` | 12px / 1.35 / +0.06em / 500 / uppercase | Stat labels, column heads, nav groups |
| `type-editorial` | 32px / 1.15 / −0.01em / 400 / serif | Marketing headings, cold-start welcome |
| `type-editorial-hero` | 48px / 1.05 / −0.015em / 400 / serif | Marketing hero headline |

The two `type-editorial` roles mirror `type-display` and `type-hero` in size, set in the display serif.
They are separate roles rather than a family class composed on top, for two reasons. Size, weight,
tracking and leading travel together here, and a serif at these sizes wants different numbers — less
negative tracking, and weight 400, because Instrument Serif ships one weight. And composition would not
work anyway: both would be one class of specificity, and Tailwind emits `type-*` after `font-*`, so the
role's 600 would win and the browser would synthesise a faux bold — on a high-contrast serif that reads
as a smeared outline rather than a heavier face.

`body` carries −0.011em of tracking. Instrument Sans, like most grotesques, is spaced for smaller optical
sizes than a screen UI runs at;
without it the copy reads slightly loose. The one figure permitted outside the scale is the health score
(48px), because it is the entire subject of its page.

`type-hero` is the seventh role and belongs to the public home screen alone — the application must not
reach for it. It reuses the health score's 48px rather than introducing a larger step, and is applied
responsively (`type-display md:type-hero`) so a phone keeps the routine size. A marketing headline is
the whole first screen and needs the weight; a page that opens into work does not.

**Motion.** One curve and one duration, set as Tailwind's defaults via
`--default-transition-duration: 150ms` and `--default-transition-timing-function: cubic-bezier(0.2, 0, 0, 1)`,
so a bare `transition-colors` inherits both without naming them at the call site. Transitions are always
property-scoped — never `transition` or `transition-all`. The curve decelerates without overshoot:
interface motion should settle, not bounce.

**Depth is layered, not cast.** There is exactly one shadow in the application, on the chart tooltip,
which genuinely floats above what it describes. Everything else separates by stacking surfaces
(`page` → `surface` → `surface-raised`) and by hairline borders. A bordered container must never
contain another bordered container; the inner block moves to `surface-raised` with no border.

**Surfaces and ink:**

| Role | Light | Dark |
|---|---|---|
| Page plane | `#f9f9f7` | `#0f0f0f` |
| Card / chart surface | `#fcfcfb` | `#17171a` |
| Raised surface | `#ffffff` | `#1f1f23` |
| Primary ink | `#0b0b0b` | `#ffffff` |
| Secondary ink | `#52514e` | `#c3c2b7` |
| Muted (axis, labels) | `#898781` | `#898781` |
| Gridline | `#e1e0d9` | `#26262b` |
| Baseline / axis | `#c3c2b7` | `#33333a` |
| Border hairline | `rgba(11,11,11,0.08)` | `rgba(255,255,255,0.10)` |

The dark page plane is deliberately off pure black: at `#000` every border above it reads as a seam.
The three dark surfaces are a few points of lightness apart, which is enough to separate a card from the
page without any of it appearing to float. Light-mode hairlines sit at 8% rather than 10% because pure
black at 10% on a near-white surface reads heavier than the same figure inverted on the dark surface.

Dark mode is a **selected** set of steps validated against the dark surface — not an inverted light
palette. Theme follows `prefers-color-scheme` with a manual override that wins in both directions.

### 2.2 Chart palette

Categorical slots, assigned in **fixed order and never cycled**:

| Slot | Hue | Light | Dark | Typical use in Frugal |
|---|---|---|---|---|
| 1 | blue | `#2a78d6` | `#3987e5` | Income · projected balance |
| 2 | orange | `#eb6834` | `#d95926` | Expense |
| 3 | aqua | `#1baf7a` | `#199e70` | Savings · net worth |
| 4 | yellow | `#eda100` | `#c98500` | Fourth series (rare) |

Validated with `scripts/validate_palette.js`:

```
light (surface #fcfcfb): lightness PASS · chroma PASS
  CVD separation  PASS  worst adjacent yellow↔aqua ΔE 9.1 (protan)
  normal-vision   PASS  worst adjacent ΔE 22.9
  contrast        WARN  aqua 2.74, yellow 2.11 — below 3:1

dark (surface #1a1a19): all five checks PASS
  CVD ΔE 8.4 · normal-vision ΔE 19.8 · contrast all ≥ 3:1
```

**The light-mode contrast WARN is a binding obligation, not a note.** Any light-mode chart using the
aqua or yellow slot must ship visible direct labels or a table view. This is enforced in the chart
component: the shared `<Chart>` wrapper requires either `directLabels` or `tableView` when those slots
are in play.

**Sequential** (magnitude — heatmaps, budget meters, forecast bands): blue, light→dark, from the
100→700 blue ramp. **Diverging** (over/under budget, above/below baseline): blue ↔ red with a neutral
gray midpoint (`#f0efec` light, `#383835` dark) — never a hue at the midpoint.

**Status colors are reserved** and never reused as a series:

| Role | Hex | Frugal meaning |
|---|---|---|
| good | `#0ca30c` | On track · `BUY_NOW` · healthy |
| warning | `#fab219` | Approaching limit · `WAIT` |
| serious | `#ec835a` | Over budget · elevated risk |
| critical | `#d03b3b` | Shortfall projected · `NOT_RECOMMENDED` |

Every status ships with an **icon and a text label**. On the light surface `warning` and `serious` sit
below 3:1 by design, so colour never carries the meaning alone — which is also what makes verdicts
legible to colorblind users and in print.

### 2.3 Chart rules

Derived from the form heuristic, applied to Frugal's specific charts:

| Chart | Form | Why |
|---|---|---|
| Net worth, savings rate, health score trend | **line**, single series | Trend over time; one series needs no legend — the title names it. |
| Income vs. expense | **grouped bar**, slots 1 + 2 | Two distinct series; identity is the job. |
| Category breakdown | **horizontal stacked bar** + table | Long category names; more than ~7 classes → the table carries the tail. Never a pie. |
| Cash-flow forecast | **line + confidence band** | p50 line in blue, p10–p90 as a 12%-opacity band of the same hue — the band is uncertainty, not a second series. |
| Budget progress | **meter** on a same-hue track | A single ratio against a limit. Not a two-slice pie. |
| Purchase impact before/after | **dumbbell** | Before → after per metric; one hue, two shades. |
| KPI headline row | **stat tiles** with delta + sparkline | Handful of headline numbers. Not a grouped bar chart. |

**Non-negotiables applied throughout:** one y-axis, never dual — net worth and savings rate are two
charts, not two scales on one. Colour follows the entity, so filtering the category list never
repaints the survivors. Marks are thin, gridlines recessive, 2px surface gap between adjacent fills,
and labels are selective rather than a number on every point. Every chart ships a crosshair-and-tooltip
hover layer and a keyboard-reachable table view.

---

## 3. Layout shell

As built (240px sidebar, no topbar — each page owns its own `h1`):

```
┌──────────────┬─────────────────────────────────────────────────────────────┐
│ Frugal       │                                                             │
│              │  Overview                             [All transactions]    │
│ ◈ Overview   │                                                             │
│ ⇄ Transactions│                       Page content                         │
│ ⛁ Receipts   │                                                             │
│              │                                                             │
│ INSIGHTS     │                                                             │
│ ♡ Health     │                                                             │
│ ↗ Forecast   │                                                             │
│ ⚗ What if?   │                                                             │
│ ★ Should I…? │                                                             │
│              │                                                             │
│ MARKET       │                                                             │
│ ⛉ Watchlist  │                                                             │
│ ⊙ Alerts     │                                                             │
│──────────────│                                                             │
│ ⚙ Priya S.   │                                                             │
│ ☀ ☾ ⌘   ⏻    │                                                             │
└──────────────┴─────────────────────────────────────────────────────────────┘
```

Nine flat destinations is past what one list holds legibly, so they are grouped into three: the
unlabelled working set, then **Insights**, then **Market**. Settings (shown as the user's display name),
the theme control, and sign-out are pinned to the footer.

The active item takes a `surface` fill plus a 2px `--brand` rail at its left edge — matched by prefix,
so `/transactions/import` keeps Transactions lit — and carries `aria-current="page"`. Nav is defined as a
single data array so the full sidebar, the collapsed rail, and the mobile tab bar cannot drift apart.

A skip-to-content link precedes the sidebar: nine links is a lot to tab past on every navigation.

**Responsive.** ≥1280px full sidebar; 768–1279px a 64px icon rail (labels become `title` attributes,
group headings stay in the accessibility tree as `sr-only`); below 768px the sidebar is replaced by a
fixed bottom tab bar. The tab bar carries Overview · Txns · Health · Advisor · Settings — five existing
destinations rather than the centre **Add** action originally sketched here, which would have introduced
a new create affordance rather than a navigation target.

---

## 4. Screen wireframes

### 4.1 Empty state — the cold-start answer

The first screen a new user sees. Given that every engine is meaningless on an empty database, this
screen's only job is to get data in.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│                    Welcome to Frugal, Priya 👋                             │
│         Frugal needs some financial history before it can advise you.      │
│                                                                            │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐        │
│   │       ⬆           │  │       ⛁          │  │       ✦          │        │
│   │  Import CSV      │  │  Snap a receipt  │  │  Try sample data │        │
│   │                  │  │                  │  │                  │        │
│   │ Bank statement   │  │ We'll read the   │  │ 12 months of      │        │
│   │ → transactions   │  │ merchant, date   │  │ realistic demo    │        │
│   │ in ~30 seconds   │  │ and total        │  │ data, instantly   │        │
│   │                  │  │                  │  │                  │        │
│   │  [ Choose file ] │  │  [ Upload ]      │  │  [ Load demo ]   │        │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘        │
│                                                                            │
│              Or  + add your first transaction manually                     │
└────────────────────────────────────────────────────────────────────────────┘
```

"Try sample data" is given equal visual weight deliberately. It is the only path that produces a fully
populated product in one click, and it is what makes the platform demonstrable to a reviewer, an
interviewer, or an investor in under five seconds.

### 4.2 Dashboard

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Overview                                        Aug 2026 ▾   [Add txn +]  │
├────────────────────────────────────────────────────────────────────────────┤
│ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌──────────────────┐│
│ │ NET WORTH     │ │ INCOME MTD    │ │ EXPENSE MTD   │ │ FINANCIAL HEALTH ││
│ │ ₹4,82,300     │ │ ₹85,000       │ │ ₹41,250       │ │       72         ││
│ │ ▲ 2.4%  ╱╲╱─  │ │ ▬ flat  ────  │ │ ▲ 8.1%  ╱╲╱   │ │  ● Low risk      ││
│ └───────────────┘ └───────────────┘ └───────────────┘ │  ▓▓▓▓▓▓▓░░░       ││
│                                                        │  Why this score →││
│                                                        └──────────────────┘│
├────────────────────────────────────────────────┬───────────────────────────┤
│  INCOME VS EXPENSE            Last 6 months    │  NEEDS ATTENTION       3  │
│  ₹                                             │                           │
│  90k ┤  ▊     ▊     ▊     ▊     ▊     ▊       │ ⚠ Food spending +23%      │
│  60k ┤  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉   │   ₹12,400 vs ₹10,080      │
│  30k ┤  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉  ▊  ▉   │   Why? →                  │
│   0  └──Mar─Apr─May─Jun─Jul─Aug────────────    │                           │
│      ▊ Income (blue)   ▉ Expense (orange)      │ ⛔ Shopping over budget    │
│                                                │   ₹8,200 of ₹6,000        │
├────────────────────────────────────────────────┤   Why? →                  │
│  WHERE IT WENT                Aug 2026         │                           │
│  ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░       │ ● New subscription        │
│  Food ₹12,400 (30%)  Rent ₹18,000 (44%) …     │   Netflix ₹649/mo         │
│                                                │   Confirm? →              │
│  Category      Amount    % of total   vs Jul   │                           │
│  Rent        ₹18,000        43.6%      ▬ 0%    ├───────────────────────────┤
│  Food        ₹12,400        30.1%     ▲ 23%    │  90-DAY OUTLOOK           │
│  Transport    ₹4,850        11.8%     ▼ 4%     │  ₹1,28,400 projected      │
│  Shopping     ₹8,200        19.9%     ▲ 41%    │  Low point ₹42,100 Sep 28 │
│                                                │  ╱╲__╱╲___╱───            │
│  [View all transactions →]                     │  Based on 142 days · EWMA │
│                                                │  Confidence: Moderate     │
└────────────────────────────────────────────────┴───────────────────────────┘
```

Design notes worth stating:

- **The category breakdown is a stacked bar plus a table**, not a pie. Categories exceed the ~7-class
  threshold where colour stops carrying meaning, and the table carries the tail while giving exact
  values and period-over-period deltas that a pie cannot.
- **The table satisfies the light-mode contrast obligation** for the aqua and yellow slots.
- **The forecast card names its method and confidence on the card itself** — "142 days · EWMA ·
  Moderate" — rather than hiding it behind a tooltip. The number and its trustworthiness carry equal weight.
- **Every insight has a "Why?" link.** No claim is asserted without its decomposition.
- Sparklines in stat tiles are unlabelled and axis-free; they carry shape, and the delta carries magnitude.

### 4.3 Receipt review — the human-in-the-loop screen

The screen that makes ~65%-accurate OCR into a trustworthy feature.

```
┌────────────────────────────────────────────────────────────────────────────┐
│  ← Receipts          Review extraction              Confidence: 71% ⚠      │
├────────────────────────────────┬───────────────────────────────────────────┤
│                                │  We read this receipt. Two fields need    │
│   ┌──────────────────────┐     │  your eyes.                               │
│   │  RELIANCE FRESH      │◀────┼─ Merchant            ✓ 96%                │
│   │  ────────────────    │     │  ┌─────────────────────────────────────┐  │
│   │  03/08/2026    14:22 │◀────┼─ │ Reliance Fresh                      │  │
│   │                      │     │  └─────────────────────────────────────┘  │
│   │  Milk 1L      ₹  62  │     │                                           │
│   │  Bread        ₹  45  │     │  Date                ✓ 91%                │
│   │  Rice 5kg     ₹ 480  │     │  ┌─────────────────────────────────────┐  │
│   │  ...                 │     │  │ 03 Aug 2026                         │  │
│   │  ────────────────    │     │  └─────────────────────────────────────┘  │
│   │  TOTAL      ₹1,2S0.00│◀════┼═ Total               ⚠ 58%  needs review  │
│   │  ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔  │     │  ┌─────────────────────────────────────┐  │
│   └──────────────────────┘     │  │ 1250.00                             │  │
│                                │  └─────────────────────────────────────┘  │
│   [⟲] [＋] [－]  [View full]   │  We read "1,2S0.00" — the S is probably   │
│                                │  a 5. Please confirm.                     │
│                                │                                           │
│   Highlighted region shows     │  Category                                 │
│   where each value was found.  │  ┌─────────────────────────────────────┐  │
│                                │  │ 🛒 Groceries              ▾   94%   │  │
│                                │  └─────────────────────────────────────┘  │
│                                │                                           │
│                                │  ⚠ Possible duplicate                     │
│                                │  ₹1,250.00 at Reliance Fresh on 3 Aug     │
│                                │  already exists.  [Compare]  [Not a dup]  │
│                                │                                           │
│                                │  [ Save as transaction ]   [ Discard ]    │
└────────────────────────────────┴───────────────────────────────────────────┘
```

- **Only low-confidence fields are flagged.** Merchant and date read cleanly, so the UI does not ask
  about them. Demanding wholesale re-verification is precisely how human-in-the-loop flows get
  abandoned.
- **The raw OCR text is shown for the failed field** — *"We read '1,2S0.00' — the S is probably a 5."*
  Showing the machine's actual reading turns a correction request into an explanation.
- **`bbox` highlighting links each field to its region** on the image, so verification is a glance
  rather than a hunt.
- **Duplicate detection surfaces before commit**, with a comparison affordance rather than a bare
  warning.
- `Save as transaction` stays disabled while any required field is below threshold — the UI mirrors the
  API's `409`, so the rule is enforced in both places.

### 4.4 Purchase advisor — the flagship

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Smart Purchase Advisor                                                    │
│  ┌──────────────────────────────────────────────────┐ ┌─────────────────┐ │
│  │ 🔍 MacBook Air M3 16GB                           │ │ ₹1,34,900       │ │
│  └──────────────────────────────────────────────────┘ └─────────────────┘ │
│                                            [ Should I buy this? ]          │
├────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │   ⏸  WAIT                                        Affordability 48/100│ │
│  │                                                   ▓▓▓▓▓░░░░░  moderate│ │
│  │   You could afford this by  15 November 2026 .                        │ │
│  │   Buying now would leave your emergency fund at 0.8 months.           │ │
│  │                                              Confidence: 77%          │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
├───────────────────────────────────┬────────────────────────────────────────┤
│  IF YOU BUY NOW                   │  WHY THIS VERDICT                      │
│                    Now    After   │                                        │
│  Liquid savings  ●────────○       │  Emergency fund after purchase         │
│                 ₹1.82L   ₹47.1k   │  0.8 months          −24.0 ▁▁▁▁▁▁▁▁   │
│                                   │  Drops from 3.2 → 0.8 months, below    │
│  Emergency fund  ●────────○       │  the 3-month floor.        weight 0.30 │
│                  3.2mo    0.8mo   │                                        │
│                                   │  Forecast trough                       │
│  Health score    ●────────○       │  ₹11,200 on 28 Sep    −8.4 ▁▁▁         │
│                    72       54    │  Little margin for a surprise expense. │
│                                   │                        weight 0.20     │
│  Savings rate    ●────────●       │                                        │
│                  51.4%   51.4%    │  Savings rate                          │
│                                   │  51.4%                +13.5 ▔▔▔▔▔     │
│  ⚠ Goal impact                    │  Strong savings mean you rebuild fast  │
│  Emergency Fund   +214 days  (P1) │  — which is why this is WAIT, not      │
│  Japan Trip        +96 days  (P3) │  NOT RECOMMENDED.       weight 0.15    │
│                                   │                                        │
├───────────────────────────────────┤  Debt-to-income                        │
│  OR PAY MONTHLY                   │  18%                  +11.2 ▔▔▔▔      │
│  ┌──────────┬─────────┬─────────┐ │  Low EMI load — an EMI route works.    │
│  │ 12 months│ ₹11,800 │ score 66│ │                        weight 0.15     │
│  │ interest │ ₹6,700  │ ▓▓▓▓▓▓░ │ │                                        │
│  ├──────────┼─────────┼─────────┤ │  Goal delay                            │
│  │ 24 months│  ₹6,200 │ score 72│ │  214 days (priority 1) −9.6 ▁▁▁▁      │
│  │ interest │ ₹13,900 │ ▓▓▓▓▓▓▓ │ │  Delays your top goal ~7 months.       │
│  └──────────┴─────────┴─────────┘ │                        weight 0.20     │
│                                   │                                        │
├───────────────────────────────────┤  ⓘ Based on 142 days of history.       │
│  CONSIDER INSTEAD                 │    Seasonal spending not yet modelled. │
│  MacBook Air M2 16GB   ₹94,900    │    Assumes income stays at current     │
│  Affordability 68 → Buy on EMI    │    level.                              │
│  [Evaluate →]                     │                                        │
└───────────────────────────────────┴────────────────────────────────────────┘
```

This screen is the product thesis made visible:

- **The verdict leads, and it is actionable** — `WAIT` carries a date, not just a discouragement.
- **The before/after is a dumbbell**, the correct form for before → after per item: one hue in two
  shades, filled dot for now, hollow for after.
- **Every factor shows its weight and signed contribution**, with contributions summing to the score.
  A rubric whose parts don't reconstruct the whole isn't an explanation.
- **One factor explains the verdict boundary** — why `WAIT` rather than `NOT_RECOMMENDED`. The contrast
  is what makes the recommendation actionable instead of merely negative.
- **Caveats sit inline at the bottom**, in the same panel as the reasoning, not buried in a tooltip.
- **The EMI table prices the alternative honestly**, showing total interest against the cash price
  rather than only the attractive monthly figure.

### 4.5 Financial health

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Financial Health                                        [How is this      │
│                                                           calculated? →]   │
├──────────────────────────────┬─────────────────────────────────────────────┤
│                              │  12-MONTH TREND                             │
│           72                 │  100 ┤                                      │
│      ▓▓▓▓▓▓▓░░░              │   75 ┤            ╱‾‾‾╲___╱‾‾              │
│                              │   50 ┤    ___╱‾‾‾                          │
│      ● Low risk              │   25 ┤ ╱‾‾                                  │
│      Confidence 81%          │    0 └─Sep─Nov─Jan─Mar─May─Jul─            │
│      ▲ 6 points vs Jul       │                                             │
├──────────────────────────────┴─────────────────────────────────────────────┤
│  WHAT MAKES UP YOUR SCORE                       weights sum to 1.00        │
│                                                                            │
│  Savings rate            51.4%      ▔▔▔▔▔▔▔▔▔▔▔  +22.1    weight 0.25  ✓  │
│  You save 51% of income, well above the 20% healthy threshold.             │
│                                                                            │
│  Emergency fund       3.2 months    ▔▔▔▔▔▔░░░░░  +13.3    weight 0.25  ⚠  │
│  Covers 3.2 months of expenses; 6 months is the target.                    │
│  → Adding ₹8,400/month reaches 6 months by Feb 2027.                       │
│                                                                            │
│  Debt-to-income            18%      ▔▔▔▔▔▔▔▔▔▔   +16.0    weight 0.20  ✓  │
│  EMIs consume 18% of income, comfortably below the 36% ceiling.            │
│                                                                            │
│  Budget discipline    4 of 5 kept   ▔▔▔▔▔▔▔▔░░   +10.8    weight 0.15  ✓  │
│  You stayed within 4 of 5 budgets over the last 3 months.                  │
│                                                                            │
│  Cash-flow stability   moderate     ▔▔▔▔▔▔░░░░    +6.2    weight 0.10  ●  │
│  Month-to-month balance varies moderately.                                 │
│                                                                            │
│  Financial growth       +2.4%/mo    ▔▔▔▔▔▔▔▔░░    +4.1    weight 0.05  ✓  │
│  Net worth is growing 2.4% per month.                                      │
│                                                        ─────────────       │
│                                                Total     72.5 / 100        │
└────────────────────────────────────────────────────────────────────────────┘
```

The visible arithmetic — six contributions summing to 72.5 under weights summing to 1.00 — is the
point. **"How is this calculated?"** opens the published rubric with bands and thresholds, so a user
who disagrees can see exactly what produced the number rather than reverse-engineering it.

Each sub-metric carries a **next action** where one exists ("Adding ₹8,400/month reaches 6 months by
Feb 2027"), which is what separates a diagnosis from advice.

### 4.6 Forecast

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Cash Flow Forecast          [30d] [60d] [90d●]     [Simulate scenario →]  │
├────────────────────────────────────────────────────────────────────────────┤
│  ₹                                                                         │
│ 1.5L ┤                                          ░░░░░░░░░░░░▁▁▁▁▁▁        │
│      │                                   ░░░░▁▁▁▁▔▔▔▔▔▔▔▔▔▔               │
│ 1.0L ┤              ░░░░░░▁▁▁▁▁▔▔▔▔▔▔▔▔                                   │
│      │       ▔▔▔▔▔▔▔                                                       │
│ 50k  ┤ ▔▔▔▔▔            ╲    ╱                                            │
│      │                    ╲╱  ← ₹42,100 low point, 28 Sep                 │
│  0   └──Aug──────Sep──────Oct──────Nov───────────────────────────         │
│         ▔▔ projected (p50)      ░░ likely range (p10–p90)                 │
├────────────────────────────────────────────────────────────────────────────┤
│  Projected 2 Nov      Lowest point        Shortfall risk                   │
│  ₹1,28,400            ₹42,100 · 28 Sep    None in 90 days  ✓               │
├────────────────────────────────────────────────────────────────────────────┤
│  HOW THIS WAS CALCULATED                                                   │
│  Method  Exponentially weighted moving average + seasonal naive            │
│  Based on  142 days (15 Mar – 4 Aug 2026)      Confidence  Moderate (65%)  │
│                                                                            │
│  Recurring income      ₹85,000/mo   Salary on the 1st, variance 0.02       │
│  Committed outflows    ₹32,400/mo   Rent, 2 EMIs, 4 subscriptions          │
│  Discretionary (EWMA)  ₹18,900/mo   Weighted mean over 142 days            │
│                                                                            │
│  ⓘ 142 days is below the 180 needed for seasonal modelling. Annual         │
│    patterns such as festival spending are not captured yet. Frugal will    │
│    switch to a seasonal model automatically at 180 days.                   │
└────────────────────────────────────────────────────────────────────────────┘
```

The confidence band is the **same hue at 12% opacity**, not a second series — uncertainty is not an
entity. The method disclosure and the upgrade path ("will switch automatically at 180 days") turn a
model limitation into a legible product behaviour.

### 4.7 Transactions

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Transactions                                    [Import] [Add txn +]      │
│  ┌──────────────┐ ┌────────┐ ┌──────────┐ ┌─────────┐  ⚠ 12 need review   │
│  │🔍 merchant…  │ │Aug 2026│ │Category ▾│ │Account ▾│                      │
│  └──────────────┘ └────────┘ └──────────┘ └─────────┘                      │
├────────────────────────────────────────────────────────────────────────────┤
│  ▢  Date    Merchant            Category           Account      Amount     │
│  ──────────────────────────────────────────────────────────────────────    │
│  ▢  3 Aug   Reliance Fresh ⛁    🛒 Groceries       HDFC      −₹1,250.00    │
│  ▢  3 Aug   Swiggy              🍽 Food Delivery   HDFC        −₹482.00    │
│  ▢  2 Aug   Unknown UPI 8821    ⚠ Uncategorised ▾  HDFC      −₹2,100.00    │
│  ▢  1 Aug   Salary — Acme       💼 Salary          HDFC     +₹85,000.00    │
│  ▢  1 Aug   Rent                🏠 Rent            HDFC     −₹18,000.00    │
│  ──────────────────────────────────────────────────────────────────────    │
│  3 selected   [Categorise ▾] [Delete]                    Load more ↓       │
└────────────────────────────────────────────────────────────────────────────┘
```

`⛁` marks a receipt-sourced row. Uncategorised rows are flagged in place with an inline picker —
correcting a category is one interaction and feeds the categoriser's training data. Infinite scroll is
cursor-backed, so inserting a transaction mid-scroll never duplicates or skips a row.

---

## 5. The shared explanation component

One component renders every engine's output. This is what makes twelve modules feel like one product.

```tsx
<ExplanationPanel
  explanation={result.explanation}
  variant="expanded" | "compact" | "inline"
/>
```

It renders, in order: verdict badge (status colour + icon + label) → score with confidence → factor
rows, each with name, value, signed contribution bar, weight, and plain-language reason → caveats.

Rules it enforces:

- Factors are rendered **generically** — the component never switches on a known factor name, so new
  rubric factors appear without a frontend change (which is why adding a factor is a non-breaking API
  change).
- Contribution bars are **diverging** from a zero baseline: positive right, negative left, gray at zero.
- An empty `factors` array renders a visible error in development and logs to Log Analytics in production.
  A recommendation without reasoning is a defect, and it should be loud.
- Caveats always render when present. They are never collapsed behind a "show more".

---

## 6. States

Every data surface implements four states. They are built at the same time as the success state, not
retrofitted.

| State | Treatment |
|---|---|
| **Loading** | Skeleton matching the final layout's shape. Never a centred spinner on a full page. |
| **Empty** | Explains what will appear and offers the action that creates it. Never a zero-value chart. |
| **Insufficient data** | The `503 INSUFFICIENT_DATA` case: states what is missing, how much more is needed, and what will unlock. |
| **Error** | Plain-language message, retry affordance, and the `request_id` in small text for support. |

The distinction between **empty** and **insufficient data** matters: "you have no transactions" and
"you have 12 days of transactions and forecasting needs 14" call for different messages, and collapsing
them into one generic empty state is how users conclude the feature is broken.

---

## 7. Accessibility

Targeting WCAG 2.1 AA (NFR-6):

- **Colour is never the sole channel.** Verdicts carry icon + label; chart series carry direct labels
  or a legend plus a table view; status states carry text.
- **Keyboard.** Full operability, visible focus rings (2px, 3:1 against the surface), logical tab
  order, skip-to-content. Charts expose a focusable table view.
- **Screen readers.** Semantic landmarks, labelled form controls, `aria-live` on job-status updates so
  receipt processing announces completion. Charts carry a text summary — *"Net worth rose 2.4% over 6
  months, from ₹4.2 lakh to ₹4.82 lakh."*
- **Motion.** `prefers-reduced-motion` disables Framer Motion transitions; nothing depends on animation
  to be understood.
- **Targets.** Minimum 44×44px on touch.
- **Zoom.** Usable to 200% without horizontal scrolling.

---

## 8. Responsive behaviour

| Breakpoint | Layout |
|---|---|
| ≥1280px | Sidebar + 12-column grid; dashboard is 2 columns; advisor is 3 columns |
| 768–1279px | Collapsed icon sidebar; dashboard single column; advisor 2 columns |
| <768px | Bottom tab bar; everything single column; tables become card lists; charts drop to ~200px height with a horizontal scroll container |

Wide content — tables, charts, the factor list — scrolls inside its own `overflow-x: auto` container.
The page body never scrolls horizontally.

---

*Next: [06-project-structure.md](06-project-structure.md)*
