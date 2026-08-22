<!--
SPDX-FileCopyrightText: (C) 2026 Drew Pang
SPDX-License-Identifier: Apache-2.0
-->

# Design: static overlay diff mode

Status: **design agreed (v2), pre-implementation review** · Branch: `drew/diff-overlay`

## Motivation

The diff UI presents changes as SMIL animations that morph elements between
their old and new states (a blink-comparator model). That is effective for
verifying a subtle change at a known location, but poor for *scanning*: only
one state is visible at any instant, the reviewer must hold the other in
memory, and a paused frame — or anything exported/printed from it — shows a
single state rather than "what changed".

The alternative model, proven by pixel-diff tools (kiri et al.), renders both
states simultaneously: **old-only geometry in one color, new-only in another,
unchanged neutral**. Everything is glanceable and any static export of the
page is itself a complete diff artifact. This doc proposes adding that as an
overlay mode alongside the existing animation, keeping the interactivity
(pan/zoom, hierarchy nav, per-element inspection) that pixel tools lack.

## Where diffs become pixels today

- `diff.py` matches and pairs objects across revisions; per-object state
  reaches rendering as `Param`s — sequences of `(value, changeset)` where
  index 0 is the base state and later entries carry the changed value(s).
- `svg.py` is the single choke point: `Svg.attr(name, value)` emits the
  base-state attribute and, when a later entry differs, queues
  `(name, old, new, changeset)`; `_flush_animate()` renders the queue as
  `<animate>`/`<animateTransform>` children of the element
  (svg.py:237-254, 294+). Element appearance/disappearance goes through
  `attr_opacity()` (svg.py:230) and `gstart(hidden=...)` the same way.
- `diffui.py:Page` renders each page via `page.fillsvg(svg, diffs, ...)`
  with `Svg(auto_animate=False)`; runtime animation timing is driven from
  `diffs.js` (the animation toolbox toggles `dur` on the emitted tags).
- `diffui.html` + `diffui.js` provide the viewer chrome: page tree, search,
  tooltips/metadata inspection, and the animation toolbox.

Consequence: the generator always knows, per attribute and per element,
whether the two states differ — the overlay needs no new diff computation,
only a different *materialization* of information already in hand.

## Proposed materialization (alternatives)

### A. Render every page twice, recolor per state

Run `fillsvg` once with all `Param`s pinned to state 0 (styled "old") and
once pinned to state 1 (styled "new"), stack the two groups.

- ✔ trivially correct, no per-element bookkeeping
- ✘ every unchanged element is drawn twice → doubled DOM, z-fighting,
  double tooltips/click targets (the stated concern), and "unchanged =
  neutral" requires blending the two copies rather than knowing it
- Rejected unless B proves impractical.

### B. Element-level state split at the emit point (recommended)

Buffer each element from tag-open to close (the `hascontents`/`nocontents`
seams already delimit this). At close:

- no queued animations and no state-dependent visibility → emit once,
  unchanged style (the overwhelmingly common case: one DOM node, tooltips
  and metadata exactly as today);
- otherwise emit two copies — one with all `Param`s resolved to the old
  state (`class="ovl-old"`), one resolved to the new (`class="ovl-new"`) —
  and no `<animate>` children. Old-only elements emit only the old copy;
  new-only elements only the new.

- ✔ single node for unchanged content (tooltip mess contained by design)
- ✔ old/new copies can share the element's metadata identity, so
  inspection can show the property-level diff for either copy
- ✔ purely a render-mode branch inside `Svg`; `diff.py`, `Page`, and the
  data model are untouched
- ✘ requires a modest `Svg` refactor: per-element output buffering and a
  "resolve Param at state i" pass (today state 0 is emitted inline and
  state 1 exists only inside the animate queue)

### C. Client-side materialization in diffui.js

Clone animated elements in the DOM, bake the endpoint values, hide the
animated originals.

- ✔ zero generator changes
- ✘ re-implements attribute semantics (`animateTransform` composition,
  opacity vs visibility) in JS; fragile against generator evolution; the
  emitted document remains animation-shaped, so any non-JS consumer of the
  SVG never benefits
- Rejected as the primary mechanism; may still be the cheapest way to
  *prototype* the visuals before committing to B.

## Decisions (2026-08-21)

- **Mechanism: dual full layers (A), operator-controlled.** Render the page
  twice — an old-state layer and a new-state layer — with **four viewer
  checkboxes**: old layer, new layer, old-layer tooltips, new-layer
  tooltips. Tooltip clutter is handled by making it switchable, not by
  suppressing information.
- **Palette: kiri convention, pinned.** Black background, white unchanged,
  red removed/old-only, blue added/new-only. Overlay mode ignores the
  active kischvidimer theme (themes keep applying to the normal and
  animated views); no attempt to blend the overlay palette with themes.
- **Tooltips: both copies, labeled** old/new in their tooltip/inspector
  chrome.

## Sheet model (decided)

Three stacked SVG groups per page, produced by rendering the page at each
rev and partitioning by element identity:

- **unchanged** (white): elements byte-identical in both renders after
  normalization, emitted once
- **removed** (red): elements only the old rev renders
- **added** (blue): elements only the new rev renders

Viewer toolbar: show/hide and tooltip toggles per sheet. "Hide unchanged"
doubles as a one-click delta-only view. Palette fixed (black background)
regardless of active theme.

Output: the one self-contained HTML kischvidimer already produces; overlay
is the default mode on open, the animated view stays reachable from the
toolbar.

## Pre-implementation validation (next step, throwaway code only)

The load-bearing assumption is that unchanged elements serialize
identically across two independent renders once uids/ids are normalized.
Before any production code:

1. **Intersection measurement** on 2-3 real pages (dense kit sheet, a page
   with a real historical diff, a page with a moved element): render both
   revs, normalize, intersect; report matched fraction on known-unchanged
   content. Anything meaningfully below 100% on an untouched sheet means
   normalization needs more than uid-stripping, and the number tells us
   where.
2. **Static visual mock** built from that output: one non-interactive HTML
   of a real diffed sheet in the three-sheet palette, for look/readability
   sign-off before any viewer work.

## Anticipated footprint (for review before code)

- `diffui.py`: Page gains a second per-rev render + partition post-pass
- one new module (partition/normalize logic)
- `diffui.html` / `diffui.js` / `diffui.css`: sheet checkboxes, overlay
  default, palette classes
- untouched: `diff.py`, `svg.py` internals (render-twice uses the existing
  single-state path), merge mode

## Known risks

- Normalization completeness (measured by the spike above)
- Embedded HTML size: changed pages carry up to 2x geometry; unchanged
  pages carry ~1x plus a second render's cost at build time
- Fork divergence: upstream (Rivos) is not accepting external contributions
  yet; this lives in the fork until that changes

## Non-goals

- PDF/raster export (HTML output only)
- Any change to diff computation, matching, or merge (`diff.py` untouched)
- Three-way/merge-mode presentation
- Theme-aware overlay palettes
