# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

import operator as op
import os
import re
import sys

from . import kicad_sym, kicad_wks, sexp, svg
from .diff import FakeDiff, Param
from .kicad_common import (
  Drawable,
  Field,
  HasInstanceData,
  HasUUID,
  Path,
  Polyline,
  Variables,
  rotated,
  transformed_pin,
  unit_to_alpha,
)
from .kicad_modifiers import HasYes
from .netlister import Netlister

# FIXME: check eeschema/schematic.keywords for completeness
#        on last check, there are around 79 unused atoms


@sexp.handler("comment")
class Comment(sexp.SExp):
  """comment entry in a title block"""

  UNIQUE = "#"
  LITERAL_MAP = {"#": 1, "text": 2}


@sexp.handler("title_block")
class TitleBlock(Drawable):
  """title_block"""

  UNIQUE = True

  @property
  @sexp.uses("title")
  def title(self):
    return self["title"][0][0] if "title" in self else None

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    svg.instantiate_worksheet(draw, context)

  @sexp.uses(
    "company",
    "comment",
    "title",
    "paper",
    "generator",
    "generator_version",
    "rev",
  )
  def fillvars(self, variables, diffs, context):
    # Fill in all variable defaults
    missing_vars = set(kicad_wks.ALL_WKS_VARS)
    for var in self.data:
      name = str(var.type)
      if name == "date":
        name = "ISSUE_DATE"
      # FIXME: move comment handling to Comment.fillvars
      if len(var.data) > 1:
        name += "".join(str(s) for s in var.data[:-1])
      name = name.upper()
      missing_vars.remove(name)
      variables.define(context + (self,), name, var.data[-1])
    for name in missing_vars:
      text = ""
      if name == "PAPER":
        for c in reversed(context):
          if c.type == "kicad_sch":
            text = c.get("paper", default=["A4"])[0]
            break
      elif name == "KICAD_VERSION":
        for c in reversed(context):
          if c.type == "kicad_sch":
            text = " ".join(
              (
                c.get("generator", default=["unknown"])[0],
                c.get(
                  "generator_version",
                  default=[
                    str(c.get("version", default=["version unknown"])[0])
                  ],
                )[0],
                "(rendered by kischvidimer)",
              )
            )
            break
      variables.define(context + (self,), name, text)
    # For whatever reason, wks uses REVISION but it references REV
    if variables.resolve(context + (self,), "REVISION") is None:
      variables.define(context + (self,), "REVISION", "${REV}")
    super().fillvars(variables, diffs, context)


@sexp.handler("junction")
class Junction(HasUUID, Drawable):
  """junction"""

  def fillnetlist(self, netlister, diffs, context):
    # TODO: at some point, confirm that there's only one valid net/bus
    netlister.add_junction(context, self)

  @sexp.uses("at")
  def pts(self, diffs=None):
    return Param.array(self["at"][0].pos(diffs, relative=False))

  @sexp.uses("diameter")
  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: this has a false per-page dependency due to netlist queries.
    #        Can this be resolved?
    if not draw & Drawable.DRAW_FG_PG:
      return
    pos = self["at"][0].pos(diffs, relative=True)
    is_bus = Netlister.n(context).get_node_count(context, pos, is_bus=True) > 0
    args = {
      "radius": sexp.Decimal("0.915") / 2,
      "color": "bus_junction" if is_bus else "junction",
    }
    self.fillsvgargs(args, diffs, context)
    args["fill"] = args.pop("color")
    svg.circle(
      pos,
      color="none",
      tag=svg.getuid(self),
      **args,
    )


@sexp.handler("no_connect")
class NoConnect(Drawable):
  """no_connect"""

  def fillnetlist(self, netlister, diffs, context):
    netlister.add_nc(context, self)

  @sexp.uses("at")
  def pts(self, diffs=None):
    return Param.array(self["at"][0].pos(diffs, relative=False))

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    xys = (
      self["at"][0]
      .pos(diffs, relative=True)
      .map(
        lambda p, sz: (
          (p[0] - sz, p[1] - sz),
          (p[0] + sz, p[1] + sz),
          p,
          (p[0] + sz, p[1] - sz),
          (p[0] - sz, p[1] + sz),
        ),
        sexp.Decimal("0.6350"),
      )
    )
    svg.polyline(xys, color="noconnect", tag=svg.getuid(self))


@sexp.handler("wire", "bus")
class Wire(HasUUID, Polyline):
  """wire or bus"""

  def fillnetlist(self, netlister, diffs, context):
    netlister.add_wire(context, self)

  def fillsvg(self, svg, diffs, draw, context):
    super().fillsvg(svg, diffs, draw, context, tag=svg.getuid(self))
    if draw & Drawable.DRAW_FG_PG and self.type == "wire":
      pts = self.pts(diffs)
      # FIXME: not the correct color?
      color = Param.only_for_base(pts, "wire", "none")
      for pos in Param.multi(2, pts):
        if Netlister.n(context).get_node_count(context, pos) == 1:
          draw_uc_at(svg, pos, color=color)


# FIXME: (hierarchical_label "x" (shape input) (at) (effects) (uuid) (property))
# FIXME: (label "x" (at) (effects) (uuid) (property))
# FIXME: (global_label "x" (shape input) (at) (effects) (uuid) (property))
@sexp.handler("global_label", "hierarchical_label", "label")
class Label(HasUUID, Drawable):
  """any type of label"""

  LITERAL_MAP = {"name": 1}

  BUS_RE = re.compile(r"(?<![_~^$]){(?!slash})(.+)}|\[(\d+)[.][.](\d+)\]")

  def fillnetlist(self, netlister, diffs, context):
    netlister.add_label(context, self)

  def fillvars(self, variables, diffs, context):
    shape = self.shape(diffs).v
    if shape is not None:
      variables.define(
        context + (self,),
        "CONNECTION_TYPE",
        "-".join(s.capitalize() for s in shape.split("-")),
      )
    super().fillvars(variables, diffs, context)
    context += (self,)
    variables.define(context, "OP", "--")
    n = Netlister.n(context)
    net = n.get_net(
      context, self.pts(diffs).v[0], bool(self.bus(diffs, context).v)
    )
    variables.define(context, "NET_NAME", net.name())
    short_name = self.net(diffs, context, display=False)  # {SLASH} is shown
    variables.define(context, "SHORT_NET_NAME", short_name)  # TODO: specificity
    # FIXME: ${NET_CLASS} -> net class
    if self.type == "global_label":
      instances = net.instances(exclude_context=context)
      refs = ",".join(f"${{{i}:#}}" for i in instances)
      variables.define(context, "INTERSHEET_REFS", refs)

  @sexp.uses("shape")
  def shape(self, diffs=None):
    return self.getparam("shape", diffs)

  def net(self, diffs, context, display=False):
    name = self.param(diffs)
    if display:
      return name.map(lambda n: n.replace("{slash}", "/"))
    return name

  def bus(self, diffs, context):
    return self.net(diffs, context).map(Label.BUS_RE.search)

  def expand_bus(self, diffs, context):
    bus = self.bus(diffs, context).v
    if not bus:
      return []
    prefix, suffix = bus.string[: bus.start()], bus.string[bus.end() :]
    # Suffix appears to get dropped silently in KiCad 8
    suffix = ""
    if bus.group(2) is not None:
      indices = (int(bus.group(2)), int(bus.group(3)))
      indices = (min(indices), max(indices) + 1)
      return [(prefix, str(n), f"{prefix}{n}{suffix}") for n in range(*indices)]
    if prefix:
      prefix += "."
    members = [m for m in bus.group(1).split(" ") if m]
    if len(members) == 1:
      for c in reversed(context):
        for ba in c.getsubs("bus_alias"):
          if ba[0] == members[0]:
            members = ba["members"][0].data
            break
        else:
          continue
        break
    return [(prefix, m, f"{prefix}{m}{suffix}") for m in members]

  @sexp.uses("at")
  def pts(self, diffs=None):
    return Param.array(self["at"][0].pos(diffs, relative=False))

  def get_text_offset(self, diffs, context, is_field):
    if self.type == "label":
      return (0, 0)
    # Only global labels appear to offset field locations
    if is_field and self.type != "global_label":
      return (0, 0)
    # Need to get effective size to calculate text height
    args = {
      "textsize": Param(1.27),
    }
    self.fillsvgargs(args, diffs, context)
    th = args["textsize"].map(lambda s: s * sexp.Decimal(svg.Svg.FONT_HEIGHT))
    offset = th.map(op.mul, sexp.Decimal("0.375"))  # DEFAULT_LABEL_SIZE_RATIO
    yoffset = 0
    # Reference: sch_label.cpp: *::CreateGraphicShape
    if self.type == "global_label":
      yoffset = th.map(op.mul, sexp.Decimal("-0.0715"))  # from sch_label.cpp
      h = th.map(op.mul, sexp.Decimal("1.5"))  # from sch_label.cpp
      shape = self.shape(diffs)
      offset = offset.map(
        lambda o, h, s: (
          o + h / 2 * (s == "input" or s in ("bidirectional", "tri_state"))
        ),
        h,
        shape,
      )
    elif self.type == "hierarchical_label":
      offset = offset.map(op.add, args["textsize"])
    elif self.type in ("hierarchical_label", "pin"):
      offset = offset.map(lambda o, s: -o - s, args["textsize"])
    offset = Param(lambda x, y: (x, y), offset, yoffset)
    if is_field:
      offset = offset.map(rotated, self["at"][0].rot(diffs))
    return offset

  @staticmethod
  def _makeoutline(xys, mx=0):
    """Makes a list of xys symmetric across X
    If mx is provided, subtracts mx from all x coordinates.
    """
    if mx:
      xys = type(xys)((p[0] - mx, p[1]) for p in xys)
    return xys and xys + type(xys)((p[0], -p[1]) for p in reversed(xys) if p[1])

  @sexp.uses("bidirectional", "input", "output", "passive", "tri_state")
  def fillsvg(self, svg, diffs, draw, context):
    pos = self["at"][0].pos(diffs, relative=True)
    svg.gstart(pos=pos, tag=svg.getuid(self))
    args = None
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_FG_PG):
      # FIXME: diffs
      args = {
        "textsize": Param(1.27),
        "color": Param(
          lambda b, t: (
            "bus"
            if b
            else "loclabel"
            if t == "label"
            else "sheetlabel"
            if t == "pin"
            else f"{t[:4]}label"
          ),
          self.bus(diffs, context),
          self.type,
        ),
      }
      self.fillsvgargs(args, diffs, context)
    if draw & Drawable.DRAW_FG:
      rot = self["at"][0].rot(diffs)
      shape = self.shape(diffs)
      dispnet = self.net(diffs, context, display=True)
      outline = None
      if self.type != "label":
        th = args["textsize"].map(lambda s: float(s) * svg.FONT_HEIGHT)
        # Reference: sch_label.cpp: *::CreateGraphicShape
        if self.type == "global_label":
          outline = shape.map(
            lambda s, w, h: Label._makeoutline(
              [(0, 0), (h / 2, h / 2), (h + w, h / 2)]
              if s == "input"
              else [(0, h / 2), (h / 2 + w, h / 2), (h + w, 0)]
              if s == "output"
              else [(0, 0), (h / 2, h / 2), (h + w, h / 2), (h * 1.5 + w, 0)]
              if s in ("bidirectional", "tri_state")
              else [(0, h / 2), (w + h / 2, h / 2)]
              # if s == "passive"
            ),
            dispnet.map(svg.calcwidth, args["textsize"]),
            th.map(op.mul, 1.5),  # from sch_label.cpp,
          )
        elif self.type in ("hierarchical_label", "pin"):
          outline = shape.map(
            lambda s, h, mx: Label._makeoutline(
              [(0, 0), (h / 2, h / 2), (h, h / 2)]
              if s == "input"
              else [(0, h / 2), (h / 2, h / 2), (h, 0)]
              if s == "output"
              else [(0, 0), (h / 2, h / 2), (h, 0)]
              if s in ("bidirectional", "tri_state")
              else [(0, h / 2), (h, h / 2)],
              # if s == "passive"
              mx,
            ),
            args["textsize"],
            args["textsize"] if self.type == "pin" else 0,
          )
      toff = self.get_text_offset(diffs, context, is_field=False)
      svg.gstart(rotate=rot)
      if outline:
        ocolor = Param(
          lambda c: c.replace("sheet", "hier") if isinstance(c, str) else c,
          args["color"],
        )
        svg.polyline(
          outline,
          color=ocolor,
          thick=args["textsize"].map(op.truediv, 8),
          close=True,
        )
      args["rotate"] = Param(lambda r: -180 * (r >= 180), rot)
      text_args = args.copy()
      text_args["textcolor"] = text_args.pop("color")
      svg.text(dispnet, prop=svg.PROP_LABEL, pos=toff, **text_args)
      svg.gend()
    if (
      draw & Drawable.DRAW_FG_PG
      and Netlister.n(context).get_node_count(
        context, pos, is_bus=bool(self.bus(diffs, context).v)
      )
      == 1
    ):
      # FIXME: not the correct color?
      uc_color = Param(
        lambda c: c.replace("sheet", "hier") if isinstance(c, str) else c,
        args["color"],
      )
      draw_uc_at(svg, (0, 0), color=uc_color)
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # tag, pos


@sexp.handler("bus_entry")
class BusEntry(Drawable):
  """Instance of a bus entry"""

  def fillnetlist(self, netlister, diffs, context):
    # TODO: at some point confirm there's exactly one bus and one net
    netlister.add_busentry(context, self)

  @sexp.uses("at", "size")
  def pts(self, diffs=None):
    pos = self["at"][0].pos(diffs, relative=False)
    size = self.getparam("size", diffs)
    return Param(
      lambda p, s: (p, (p[0] + s[0], p[1] + s[1])),
      pos,
      size,
    )

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    pts = Param.multi(2, self.pts(diffs))
    args = {
      "p1": pts[0],
      "p2": pts[1],
      "color": "wire",
      "tag": svg.getuid(self),
    }
    self.fillsvgargs(args, diffs, context)
    args.pop("size")
    svg.line(**args)


@sexp.handler("rule_area")
class RuleArea(HasUUID, Drawable):
  """A rule area; contains a polyline"""


@sexp.handler("netclass_flag")
class NetclassFlag(HasUUID, Drawable):
  """A net directive label"""

  def fillnetlist(self, netlister, diffs, context):
    # TODO: add netclass to location, fix nodecount, etc
    pass

  @sexp.uses("shape")
  def shape(self, diffs=None):
    """ "dot" "rectangle" "round" "diamond" """
    return self.getparam("shape", diffs, default="round")

  @sexp.uses("at")
  def pts(self, diffs=None):
    return Param.array(self["at"][0].pos(diffs, relative=False))

  def fillsvg(self, svg, diffs, draw, context):
    pos = self["at"][0].pos(diffs, relative=True)
    svg.gstart(pos=pos)  # tag=svg.getuid(self))
    args = None
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_FG_PG):
      # FIXME: diffs
      args = {"textcolor": "netclass_refs"}
      self.fillsvgargs(args, diffs, context)
    if draw & Drawable.DRAW_FG:
      rot = self["at"][0].rot(diffs)
      size = Param(sexp.Decimal("0.915"))
      length = self.getparam("length", diffs, default=0)
      shape = self.shape(diffs)
      ocolor = args["textcolor"]
      svg.gstart(rotate=rot)
      xys = shape.map(
        lambda sh, h, s: (
          (
            (0, 0),
            (0, s / 2 - h),
            (s, s / 2 - h),
            (s, -s / 2 - h),
            (-s, -s / 2 - h),
            (-s, s / 2 - h),
            (0, s / 2 - h),
          )
          if sh == "rectangle"
          else (
            (0, 0),
            (0, s / 2 - h),
            (s, -h),
            (0, -s / 2 - h),
            (-s, -h),
            (0, s / 2 - h),
          )
          if sh == "diamond"
          else ((0, 0), (0, s / 2 - h))
          # if shape in ("dot", "round")
        ),
        length,
        size,
      )
      circle = {
        "pos": length.map(lambda h: (0, -h)),
        "radius": size.map(op.truediv, 2),
        "color": shape.map(lambda s, c: c if s == "round" else "none", ocolor),
        "fill": shape.map(lambda s, c: c if s == "dot" else "none", ocolor),
      }
      svg.polyline(xys=xys, color=ocolor)
      svg.circle(**circle)
      svg.gend()
    if (
      draw & Drawable.DRAW_FG_PG
      and Netlister.n(context).get_node_count(context, pos, is_bus=False) <= 1
    ):
      uc_color = args["textcolor"]
      draw_uc_at(svg, (0, 0), color=uc_color)  # FIXME: not the correct color?
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # pos,  # tag


@sexp.handler("table")
class Table(Drawable):
  """Top table instance; defines extra properties at the table level."""

  def __str__(self):
    pos = self["cells"][0].pos()
    return f"{self.type} at ({pos.v[0]}, {pos.v[1]})"

  def fillsvg(self, svg, diffs, draw, context):
    pos = self["cells"][0].pos(diffs)
    svg.gstart(pos=pos)
    if draw & Drawable.DRAW_FG:
      widths = self.getparam("column_widths", diffs)
      heights = self.getparam("row_heights", diffs)
      width = widths.map(sum)
      height = heights.map(sum)
      border_style = {"color": "notes"}
      self["border"][0].fillsvgargs(border_style, diffs, context + (self,))
      sep_style = {"color": "notes"}
      self["separators"][0].fillsvgargs(sep_style, diffs, context + (self,))
      border_style.setdefault("color", "notes")
      sep_style.setdefault("color", "notes")

      # render external border. external border is under inner border in Z
      has_external = self["border"][0].has_yes("external")
      svg.gstart(hidden=has_external.map(op.not_))
      svg.rect(width=width, height=height, **border_style)
      svg.gend()

      # render header
      has_header = self["border"][0].has_yes("header")
      svg.gstart(hidden=has_header.map(op.not_))
      svg.line(
        p1=heights.map(lambda hs: (0, hs[0])),
        p2=heights.map(lambda hs, w: (w, hs[0]), width),
        **border_style,
      )
      svg.gend()

      # render inner horizontal lines (special-case header)
      has_row_sep = self["separators"][0].has_yes("rows")
      svg.gstart(hidden=has_row_sep.map(op.not_))
      svg.lines(
        xys=heights.map(
          lambda hs, w, has_header: [
            pt
            for i in range(has_header + 1, len(hs))
            for y in (sum(hs[:i]),)
            for pt in ((0, y), (w, y))
          ],
          width,
          has_header,
        ),
        **sep_style,
      )
      svg.gend()

      # render inner vertical lines
      has_col_sep = self["separators"][0].has_yes("cols")
      svg.gstart(hidden=has_col_sep.map(op.not_))
      svg.lines(
        xys=widths.map(
          lambda ws, h: [
            pt
            for i in range(1, len(ws))
            for x in (sum(ws[:i]),)
            for pt in ((x, 0), (x, h))
          ],
          height,
        ),
        **sep_style,
      )
      svg.gend()
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # pos


@sexp.handler("border", "separators")
class TableLines(Drawable):
  UNIQUE = True


@sexp.handler("cells")
class Cells(Drawable):
  """Contains all the cells of a table. Actual cells look like TextBoxes."""

  UNIQUE = True

  def pos(self, diffs=None):
    """Return the top-left cell location."""
    return self["table_cell"][0].pos_size(diffs, raw_pos=True)[0]

  def to_row_col(self, coord, relative=False):
    """Returns a tuple of row#, col# for a coordinate"""
    # Does not rely on any sorting of the cells.
    # Count the number of cells up-left of the coordinate and the number of
    # unique X coordinates. The number of unique X coordinates is the column and
    # the count divided by that is the row number.
    xs = set()
    count = 0
    for cell in self["table_cell"]:
      pos = cell["at"].pos(relative=relative).v
      if pos[0] <= coord[0] and pos[1] <= coord[1]:
        xs.add(pos[0])
        count += 1
    return count // len(xs), len(xs) - 1


Reference = sexp.SExp.basic("reference")  # Instance data reference
Unit = sexp.SExp.basic("unit")  # SymbolInst/Instance data unit
BodyStyle = sexp.SExp.basic("body_style", "convert")  # SymbolInst: sym variant
LibId = sexp.SExp.basic("lib_id")  # ID of a symbol back to the original library
LibName = sexp.SExp.basic("lib_name")  # ID of a local symbol override
Mirror = sexp.SExp.basic("mirror")  # SymbolInst: mirrors
Shape = sexp.SExp.basic("shape")  # specifies the shape of a label/pin
ColumnWidths = sexp.SExp.basic("column_widths", istuple=True)  # table params
RowHeights = sexp.SExp.basic("row_heights", istuple=True)  # table params

Cols = HasYes.handler("cols")  # table border params
Dnp = HasYes.handler("dnp")  # SymbolInst/Sheet
External = HasYes.handler("external")  # table border params
Header = HasYes.handler("header")  # table border params
Rows = HasYes.handler("rows")  # table border params


# FIXME: (symbol (lib_id "x") (at) (unit 1) (property) (pin)
#          (instances (project "x" (path "y" (reference "z") (unit 1)))))
class SymbolInst(HasUUID, HasInstanceData, Drawable):
  """An instance of a symbol in a schematic"""

  def fillvars(self, variables, diffs, context):
    variables.define(context + (self,), "UNIT", self.unit(diffs, context, True))
    variables.define(context + (self,), "OP", "--")
    """ FIXME:
    ${ref:DNP} -> "DNP" or ""
    ${ref:EXCLUDE_FROM_BOARD} -> "Excluded from board" or ""
    ${ref:EXCLUDE_FROM_BOM} -> "Excluded from BOM" or ""
    ${ref:EXCLUDE_FROM_SIM} -> "Excluded from simulation" or ""
    ${ref:FOOTPRINT_LIBRARY} -> footprint field (prior to colon if present)
    ${ref:FOOTPRINT_NAME} -> footprint field, after colon. blank if no colon
    ${ref:NET_CLASS(<pin_number>)} -> net class of attached net to pin
    ${ref:NET_NAME(<pin_number>)} -> connection name of net attached to pin
    ${ref:PIN_NAME(<pin_number>)} -> name of the pin
    ${ref:SHORT_NET_NAME(<pin_number>)} -> local name of the net attached to pin
    ${ref:SYMBOL_DESCRIPTION} -> description from the library cache
    ${ref:SYMBOL_KEYWORDS} -> keywords from the library cache
    ${ref:SYMBOL_LIBRARY} -> library name
    ${ref:SYMBOL_NAME} -> symbol name
    """
    super().fillvars(variables, diffs, context)

  def lib_id(self, diffs, context):
    # lib_name specifies page-local overrides of the original library symbol,
    # usually due to out-of-date symbol instances.
    p = self.getparam(
      "lib_name", diffs, default=self.getparam("lib_id", diffs, default="")
    )
    # Ensure none of the diffs are ever empty, since that would look glitchy
    p = Param(p, default=p.reduce(max))
    return p

  def get_alternates(self, diffs, context):
    # TODO: redo alternates system.  SymbolInst.get_alternates is only used by
    # svg.instantiate (to generate the instance hash) and by PinDef.alternate
    # (lookup by pin number). netlister uses SymbolDef.get_nonunique_pins
    # which only checks base pin name uniqueness (no alternates dependency).
    # get_alternates is still slow/requires caching.
    # Instead, only have two entries in the symbol library: one with all pins
    # at their default configuration and another with none of the
    # alternate-enabled pins rendered. When rendering a unit with any
    # alternates (across any diff), use the latter type and manually render
    # all of the alternate pins.
    # This may require updates to the javascript code for mouseovers, although
    # that could be helpful to show the original pin name for alternates.
    cache_key = id(diffs)
    if not hasattr(self, "_get_alternates_cache"):
      self._get_alternates_cache = {}
    if cache_key in self._get_alternates_cache:
      return self._get_alternates_cache[cache_key]
    added, removed = self.added_and_removed(diffs, PinInst)
    context = context + (self,)
    nums = []
    alt_params = []
    for pin in self["pin"] if "pin" in self else ():
      rm_c = removed.get(id(pin))
      if rm_c is not None:
        alt = pin.get_alternate(None, context)
        if alt is not None:
          nums.append(pin.number)
          alt_params.append(FakeDiff(rm_c, old=alt.v).param())
      else:
        alternate = pin.get_alternate(diffs, context)
        if alternate is not None:
          nums.append(pin.number)
          alt_params.append(alternate)
    for pin, add_c in added:
      alt = pin.get_alternate(None, context)
      if alt is not None:
        nums.append(pin.number)
        alt_params.append(FakeDiff(add_c, new=alt.v).param())
    if not alt_params:
      result = Param({})
    else:
      result = Param(
        lambda *alts, nums=nums: {
          num: a for num, a in zip(nums, alts) if a is not None
        },
        *alt_params,
      )
    self._get_alternates_cache[cache_key] = result
    return result

  def fillnetlist(self, netlister, diffs, context):
    # Fill in all pins. Use Svg's transformation implementation
    lib = context[-1]["lib_symbols"][0]
    lib_id = self.lib_id(diffs, context).v
    sym = lib.symbol(lib_id)
    unit = self.unit(diffs, context)
    variant = self.variant(diffs, context)
    sym.fillnetlist(
      netlister,
      diffs,
      context + (self,),
      unit=unit,
      variant=variant,
    )

  def transform_pin(self, pos, _diffs=None):
    # FIXME: diffs
    # Only used by PinDef.pts, which in turn is only used by the netlister.
    # Matches what's done in Svg with gstart.
    return transformed_pin(
      pos,
      *self.rot_mirror().v,
      self["at"][0].pos().v,
    )

  def fillsvg(self, svg, diffs, draw, context):
    # Decide what to draw
    subdraw = Drawable.DRAW_BG if draw & Drawable.DRAW_SYMBG else 0
    if draw & Drawable.DRAW_SYMFG:
      subdraw |= Drawable.DRAW_PINS | Drawable.DRAW_FG | Drawable.DRAW_TEXT
    dnp = self.dnp(diffs).map(lambda dnp: "dim" * dnp)
    sym_pos = self["at"][0].pos(diffs, relative=True)
    svg.gstart(pos=sym_pos, path=self.uuid(generate=True))
    if subdraw:
      # FIXME: diffs, of course
      lib = context[-1].get_symbol_lib(diffs, context)
      lib_id = self.lib_id(diffs, context)
      rot = self.rot(diffs)
      mirror = self.mirror(diffs)
      unit = self.unit(diffs, context)
      variant = self.variant(diffs, context)
      svg.gstart(rotate=rot, mirror=mirror)
      svg.gstart(filt=dnp)
      bounds = svg.instantiate(
        subdraw,
        lib,
        lib_id,
        unit=unit,
        variant=variant,
        diffs=diffs,
        context=(self,),
      )
      # Draw unconnected circles
      if subdraw & Drawable.DRAW_PINS:  # FIXME: should this be DRAW_FG_PG?
        # TODO: Netlists are currently only valid for the base, so hide
        # FIXME: only_for_base isn't getting a full picture of diffs...
        # FIXME: not the correct color
        uc_color = Param.only_for_base(sym_pos, "device", "none")
        n = Netlister.n(context)
        # FIXME: diffs. hide unconnected circles on diffs
        # NOTE: context passed to sym (intentionally) does not include self, so
        #       returned pts will be untransformed
        sym = lib.symbol(lib_id.v, diffs)
        for pos in sym.get_con_pin_coords(diffs, context, unit, variant):
          abs_pos = transformed_pin(pos, rot, mirror, sym_pos)
          if n.get_net(context, abs_pos).is_floating_sympin():
            svg.circle(
              pos=Param(lambda p: (p[0], -p[1]), pos),
              radius=sexp.Decimal(0.3175),
              fill="none",
              color=uc_color,
              thick="ui",
            )
      svg.gend()  # dim
      if bounds and subdraw & Drawable.DRAW_FG:
        draw_dnp(dnp, svg, bounds)
      svg.gend()  # rot, mirror
    svg.gstart(filt=dnp)
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # dim
    svg.gend()  # pos, path

  def show_unit(self, diffs, context):
    for c in reversed(context):
      if c.type == "kicad_sch":
        lib = c.get_symbol_lib(diffs, context)
        lib_id = self.lib_id(diffs, context)
        symbols = lib_id.map(lambda lib_id, lib: lib.symbol(lib_id, diffs), lib)
        return symbols.map(lambda s: s.show_unit(diffs, context))
    return Param(True)

  def rot(self, diffs=None):
    return self["at"][0].rot(diffs)

  @sexp.uses("mirror")
  def mirror(self, diffs=None):
    return self.getparam("mirror", diffs)

  @sexp.uses("x", "y")
  def rot_mirror(self, diffs=None):
    # Returns a simplified rot + mirror, where mirror is never "y"
    return Param(
      lambda r, m: ((r + 180) % 360, "x") if m == "y" else (r, m),
      self.rot(diffs),
      self.mirror(diffs),
    )

  def dnp(self, diffs=None):
    return self.has_yes("dnp", diffs)

  def refdes(self, diffs, context):
    return HasInstanceData.lookup(
      "reference",
      diffs,
      context + (self,) if context else None,
      default=Field.getprop(self, "Reference", diffs, default="?"),
    )

  def unit(self, diffs, context, as_alpha=False):
    unit = HasInstanceData.lookup(
      "unit",
      diffs,
      context + (self,) if context else None,
      default=self.getparam("unit", diffs, default=1),
    )
    if not as_alpha:
      return unit
    return unit.map(unit_to_alpha)

  def variant(self, diffs, context):
    return Param(
      self.getparam(
        "body_style", diffs, default=self.getparam("convert", diffs, default=1)
      )
    )

  def power_net(self, diffs, context, netprefix="/"):
    """If power symbol, return the net; if local, include netprefix"""
    lib = context[-1].get_symbol_lib(diffs, context)
    lib_id = self.lib_id(diffs, context)
    sym = lib.symbol(lib_id, diffs)
    power_type = sym.map(lambda s: s.getparam("power", None))
    if power_type.is_empty:
      return power_type  # effectively Param(None)
    netprefix = power_type.map(
      lambda t, n: n.rstrip("/") + "/" if t == "local" else "", netprefix
    )
    # Power symbols use the Value as the net
    variables = Variables.v(context)
    value = Field.getprop(self, "Value", diffs)
    power_net = value.map(
      lambda v, n, lib_id: (
        n + variables.expand(context + (self,), v)
        if v
        else n + lib_id.rpartition(":")[2]
      ),
      netprefix,
      lib_id,
    )
    return power_type.map(lambda t, n: None if t is None else n, power_net)

  def as_comp(self, context):
    # returns (refdes, {dict of properties})
    # Special properties:
    #   chr(1): local uuid
    #   chr(2): lib_id
    #   chr(3): dnp
    props = {
      chr(1): self.uuid(generate=True),
      chr(2): self.lib_id(None, context).v,
      "Reference": self.refdes(None, context).v,
    }
    if self.dnp().v:
      props[chr(3)] = True
    if "property" not in self:
      return props["Reference"], props
    variables = Variables.v(context)
    for prop in self["property"]:
      name = prop.name
      value = variables.expand(context + (self,), prop.value)
      if (
        name
        and name != "Reference"
        and not name.lower().startswith("sim.")
        and value
        and value != "~"
      ):
        props[name] = value
    return props["Reference"], props


kicad_sym.SymbolInst = SymbolInst


class AlternateInst(sexp.SExp):
  """Alternate selection on a pin"""

  LITERAL_MAP = {"name": 1}


kicad_sym.AlternateInst = AlternateInst


class PinInst(HasUUID, sexp.SExp):
  """pins in a symbol instance"""

  LITERAL_MAP = {"number": 1}

  @property
  def number(self):
    return self[0]

  @sexp.uses("alternate")
  def get_alternate(self, diffs, context):
    return self.getparam("alternate", diffs, cls=kicad_sym.AlternateInst)


kicad_sym.PinInst = PinInst


class PinSheet(Label):
  """A pin on a sheet instance"""

  LITERAL_MAP = {"name": 1, "shape": 2}

  def fillnetlist(self, netlister, diffs, context):
    netlister.add_sheetpin(context, self)

  def shape(self, diffs=None):
    return self.param(diffs, "shape")


kicad_sym.PinSheet = PinSheet


@sexp.handler("bus_alias")
class BusAlias(sexp.SExp):
  """Before KiCad 9.0, bus alias definitions were stored as a sheet object."""

  UNIQUE = "alias"
  LITERAL_MAP = {"alias": 1}


@sexp.handler("member")
class Member(sexp.SExp):
  """Before KiCad 9.0, bus alias definitions were stored as a sheet object."""

  LITERAL_MAP = {"members": (1, -1)}
  # FIXME: members are a list of literals; this needs custom diff handling.


@sexp.handler("sheet")
class Sheet(HasUUID, HasInstanceData, Drawable):
  """Sheet instance"""

  @classmethod
  def fake(cls, uuid):
    """Creates a fake, incomplete sheet element for the purposes of UUIDs"""
    if isinstance(uuid, (HasUUID, KicadSch)):
      uuid = HasUUID.uuid(uuid)
    assert isinstance(uuid, str)
    return cls.new(sexp.SExp.init([sexp.Atom("uuid"), uuid]))

  def fillvars(self, variables, diffs, context):
    super().fillvars(variables, diffs, context)
    context = context + (self,)
    variables.define(context, "FILENAME", os.path.basename(self.file(diffs).v))
    variables.define(context, "FILEPATH", self.file(diffs).v)
    # Define SHEETPATH using the parent sheetpath and just-now-defined sheetname
    # The resolver handles the recursion by going up in the hierarchy
    variables.define(context, "SHEETPATH", "${SHEETPATH}${SHEETNAME}/")

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs, of course
    pos = self["at"][0].pos(diffs, relative=True)
    dnp = self.dnp(diffs).map(lambda dnp: "dim" * dnp)

    svg.gstart(pos=pos, path=self.uuid(generate=True))
    svg.gstart(filt=dnp)

    # Draw the rectangle
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_BG):
      args = {
        "color": "sheet",
        "fill": "sheet_background",
      }
      self.fillsvgargs(args, diffs, context)
      args["width"], args["height"] = Param.multi(2, args.pop("size"))
      if not draw & Drawable.DRAW_FG:
        args["thick"] = 0
      if not self.draw_body(draw, args):
        args["fill"] = "none"
      svg.rect(**args)

    # Draw the rest of the owl
    super().fillsvg(svg, diffs, draw, context)

    svg.gend()  # dim
    if draw & Drawable.DRAW_FG:
      # FIXME: in the sheet case, margins for the X include property text
      margin = sexp.Decimal("1.27")
      draw_dnp(
        dnp,
        svg,
        Param(
          lambda w, h, m: (-m, -m, w + m, h + m),
          args["width"],
          args["height"],
          margin,
        ),
      )

    svg.gend()  # path, pos

  @sexp.uses("dnp")
  def dnp(self, diffs=None):
    return self.has_yes("dnp", diffs)

  def name(self, diffs):
    return Field.getprop(self, "Sheetname", diffs)

  def file(self, diffs):
    return Field.getprop(self, "Sheetfile", diffs)


@sexp.handler("kicad_sch")
class KicadSch(Drawable):  # ignore the uuid for the most part
  """A schematic page"""

  UNIQUE = True  # although we'll want to ignore this when handling renames

  def __str__(self):
    return os.path.basename(getattr(self, "_fname", "kicad_sch"))

  @property
  def paper(self):
    return self.get("paper", "A4")[0]

  # Stuff for diffui
  def initsch(self, fname=None):
    self._fname = fname

  def relpath(self, fname):
    d = os.path.dirname(self._fname)
    return f"{d}/{fname}" if d else fname

  @property
  def title(self):
    return self["title_block"][0].title if "title_block" in self else None

  @property
  @sexp.uses("sheet_instances")
  def root_path(self):
    # Returns the path element if this is a root sheet; None otherwise
    # NOTE: if this ever stops working (eg sheet_instances is removed), a
    # potential heuristic is to look at the various instances fields on the
    # page and see if the page's UUID is featured first.
    if (
      "sheet_instances" in self
      and "path" in self["sheet_instances"][0]
      and self["sheet_instances"][0]["path"][0][0] == "/"
    ):
      return self["sheet_instances"][0]["path"][0]
    return None

  def is_root(self, context=None):
    # Returns True if this is the root page, based on context if available
    if context:
      elements = [""]
      for c in context:
        if c.type == "path":
          elements = [c.uuid()]
        elif isinstance(c, HasUUID):
          elements.append(c.uuid(generate=True))
      if "/".join(elements).count("/") > 1:
        return False
    return self.root_path is not None

  def fillsvg(self, svg, diffs, draw, context):
    if "title_block" not in self:
      new_context = (context or ()) + (self,)
      TitleBlock.new().fillsvg(svg, diffs, draw, new_context)
    super().fillsvg(svg, diffs, draw, context)

  def fillvars(self, variables, diffs, context=None):
    if "title_block" not in self:
      new_context = (context or ()) + (self,)
      TitleBlock.new().fillvars(variables, diffs, new_context)
    if self.is_root(context):
      variables.define(context, "FILENAME", os.path.basename(self._fname))
      variables.define(context, "FILEPATH", self._fname)
      variables.define(context, "SHEETFILE", os.path.basename(self._fname))
      variables.define(context, "SHEETPATH", "/")
    super().fillvars(variables, diffs, context)

  def get_symbol_lib(self, diffs, context):
    """Always returns a SymLib instance, even if it was added or rm'd."""
    # TODO: handle the case where a page is blank and two diffs add symbols.
    #       it's possible this is never an issue if blank pages always have a
    #       blank symbol_lib entry.
    # Added/removed symbol libs are returned as if they're normal to avoid
    # rendering glitches.
    added, _removed = self.added_and_removed(diffs, kicad_sym.SymLib)
    if "lib_symbols" in self:
      return self["lib_symbols"][0]
    if added:
      assert len(added) == 1, "add-add conflicts not supported"
      return added[0]
    return kicad_sym.SymLib.new()

  def inferred_instances(self, project=None, diffs=None):
    """If operating on a standalone file, we won't have any context on
    instances. So come up with the different instance views."""
    instances = set()
    # Instances can be inferred from sheet and symbol instantiations
    for typ in "sheet", "symbol":
      added, _removed = self.added_and_removed(diffs, sexp.SExp.get_class(typ))
      all_items = [a.v for a in added] + (self[typ] if typ in self else [])
      for obj in all_items:
        instances.update(obj.paths(project))
    if not instances:
      instances.add("/" + HasUUID.uuid(self, generate=True))
    return [
      (Sheet.fake(i.rpartition("/")[0]), Sheet.fake(i.rpartition("/")[2]))
      for i in instances
    ]

  def get_sheets(self, project=None, diffs=None):
    """Returns a list of tuples of (path, sheetref)"""
    # FIXME: includes adds from from diffs
    added, _removed = self.added_and_removed(diffs, Sheet)
    alls = [a.v for a in added] + self.getsubs("sheet")
    sheets = []
    for sheet in alls:
      sheets.extend((p, sheet) for p in sheet.paths(project).values())
    return sheets

  def get_components(self, context, instance, diffs=None):
    # returns a dict mapping refdes to dict of properties
    # Context should include project and variables, ideally
    added, _removed = self.added_and_removed(diffs, SymbolInst)
    alls = [a.v for a in added] + self.getsubs("symbol")
    context += (Path.new(instance), self)
    comps = {}
    for sym in alls:
      ref, data = sym.as_comp(context)
      assert not any(isinstance(k, Param) for k in data)
      # Un-diff everything (TODO: use the data?)
      data = {k: v.v if isinstance(v, Param) else v for k, v in data.items()}
      if not ref.startswith("#"):
        comps.setdefault(ref, []).append(data)
    return comps

  # def get_nets(self, instance, variables, include_power=True):
  #  # FIXME: include_power -> include symbols with invisible power_input pins
  #  #        I think these can be variable-defined, unfortunately
  #  # Just get local nets for now
  #  # FIXME: properly return connection names, in addition to somehow indexing
  #  #        local nets
  #  variables = variables or Variables()
  #  nets = set()
  #  for labtyp in "global_label", "hierarchical_label", "label":
  #    if labtyp not in self:
  #      continue
  #    for label in self[labtyp]:
  #      nets.add(label.net(None, instance).v)
  #  return nets


# Set kicad_sch.data_filter_func to be able to change sch files as they load
def kicad_sch(f, fname=None):
  data = f.read()
  if isinstance(data, bytes):
    data = data.decode()
  if hasattr(kicad_sch, "data_filter_func"):
    data = kicad_sch.data_filter_func(data)
  data = sexp.parse(data)
  if isinstance(data[0], KicadSch):
    data[0].initsch(fname)
    if "version" in data[0] and data[0]["version"][0].is_supported:
      return data[0]
  return None


def draw_dnp(dnp, svg, bounds):
  # FIXME: SCH_SYMBOL::PlotDNP uses a fancy calculation for margins which comes
  # out to -0.4x the pin length if pins exist on an edge, and -0.7x other pin
  # length if the edge has none. Sheets are even wonkier, including properties.
  margin = 0.4 * 1.27
  corners = bounds.map(
    lambda b, m: (
      float(b[0]) + m,
      float(b[1]) + m,
      float(b[2]) - m,
      float(b[3]) - m,
    ),
    margin,
  )
  pts = corners.map(lambda c: (c[:2], c[2:], (c[0], c[3]), (c[2], c[1])))
  svg.lines(
    pts,
    color=dnp.map(lambda dnp: "dnp_marker" if dnp else "none"),
    thick="dnp",
  )


def draw_uc_at(svg, pos, color):
  sz = 0.6  # FIXME: number?
  pos = Param(
    lambda p, sz: (float(p[0]) - sz / 2, float(p[1]) - sz / 2), pos, sz
  )
  svg.rect(
    pos=pos,
    width=sz,
    height=sz,
    color=color,
    thick="ui",
  )


def main(argv):
  """USAGE: kicad_sch.py [kicad_sch [instance]]
  Reads a kicad_sch from stdin or symfile and renders a random instance or the
  specified instance as an svg to stdout.
  """
  s = svg.Svg(theme="default")
  path = argv[1] if len(argv) > 1 else None
  with open(path) if path else sys.stdin as f:
    data = kicad_sch(f, path)
  variables = Variables()
  data.fillvars(variables, None, None)
  data.fillsvg(s, None, Drawable.DRAW_ALL, variables.context())
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
