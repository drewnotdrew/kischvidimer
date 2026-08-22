<!--
SPDX-FileCopyrightText: (C) 2026 Drew Pang
SPDX-License-Identifier: Apache-2.0
-->

# Design: static overlay diff mode

Status: **draft / open questions pending** · Branch: `drew/diff-overlay`

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

## The white-unchanged problem (open)

Pure dual-layer rendering colors *whole layers* red and blue; unchanged
geometry exists in both layers and overlaps exactly. Making that overlap
read as **white** (the kiri look) has two candidate solutions:

1. **Blend emulation**: `mix-blend-mode: lighten` on the layers gives
   red+blue = magenta, not white; kiri reaches white via a third
   green-where-both-overlap pass driven by rasterized alpha masks.
   Reproducing that with live vector layers means duplicating both layer
   trees inside SVG `<mask>` elements — 4x geometry on sheets that are
   already MB-scale. Faithful, but likely heavy.
2. **Common-element split (recommended)**: render the two state layers as
   in A, then partition by set-intersection of serialized elements:
   present-in-both -> one white "unchanged" layer; old-only -> red layer;
   new-only -> blue layer. Still render-twice (no Param/Svg surgery), adds
   only a post-pass. Bonus: unchanged content exists once, so its tooltips
   aren't duplicated. Implementation note: element serialization must be
   normalized before comparison (uids/ids differ between the two render
   passes) — this is the main piece of real work.

Option 2 changes the checkbox story slightly (three layers: unchanged /
removed / added — plus tooltip toggles; whether tooltips need a per-layer
toggle for the unchanged layer is a taste call).

## Open questions (blocking)

1. White-unchanged: blend emulation (1) or common-element split (2)?
   Split changes "four checkboxes" into a 3-layer x 2 (or 3+2) matrix.
2. Default state of the generated HTML: when CI publishes a diff page,
   should it open already in overlay mode with these layers on, or open in
   today's animated view with overlay opt-in via the toolbar? (This is all
   the earlier "scope" question meant.)

## Non-goals

- PDF/raster export (HTML output only, per current direction)
- Any change to diff computation, matching, or merge (`diff.py` untouched)
- Three-way/merge-mode presentation (overlay is a two-state view; merge
  keeps the existing UI)
- Theme-aware overlay palettes
