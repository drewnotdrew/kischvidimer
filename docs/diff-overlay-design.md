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

## Presentation

- Colors via a small fixed class set (`.ovl-old`, `.ovl-new`), overriding
  stroke/fill. Baseline palette per kiri convention: old = red, new = blue.
  Whether unchanged content renders as normal theme colors (dimmed?) or is
  forced to a neutral (kiri renders it white-on-black) is an open question —
  the theme machinery (`themes.py`) can support either.
- The overlay is a mode of the existing diff view, toggled from the toolbar
  alongside the animation toolbox (overlay ⇄ animate are mutually
  exclusive; the animation remains available for subtle-move verification).
- Output stays the self-contained HTML that `diffui.py` already produces;
  no new output format. A follow-on (out of scope here) could rasterize the
  same overlay SVGs for paginated artifacts.

## Interactivity in overlay mode (open)

Unchanged elements keep today's behavior. For changed pairs the open
questions are how the two copies respond to hover/click:

1. both copies hoverable, tooltip annotated "old"/"new"; or
2. hover/click targets only the new copy (old copy pointer-transparent),
   with the inspector showing the full before/after property diff; or
3. pairs act as one target: hovering either highlights both + shows the
   property diff.

(3) is the most useful and the most work; (2) is a clean default.

## Open questions (blocking)

1. **Mechanism**: proceed with B (generator-side split)? Prototype via C
   first to validate the visuals cheaply?
2. **Unchanged-content styling**: normal theme colors, dimmed theme colors,
   or kiri-style neutral white-on-dark?
3. **Interactivity model** for changed pairs: option 1/2/3 above.
4. **Scope**: viewer toggle only, or also a CLI flag that emits the HTML
   with overlay as the default mode (for the schematic-hosting flow)?
5. **Upstreamability**: keep the fork patch minimal in case this is worth
   offering upstream (Rivos CLA pending), or optimize purely for the Teleo
   fork?

## Non-goals

- PDF/raster export (HTML output only, per current direction)
- Any change to diff computation, matching, or merge (`diff.py` untouched)
- Three-way/merge-mode presentation (overlay is a two-state view; merge
  keeps the existing UI)
