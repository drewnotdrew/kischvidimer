# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
# SPDX-FileCopyrightText: Copyright 2024 Google LLC
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

"""
Parses Kicad worksheet files
"""

import os
import re
import sys

from . import sexp, svg
from .diff import Param
from .kicad_common import Drawable, Variables, unit_to_alpha

# NOTE: check common/drawing_sheet/drawing_sheet.keywords for completeness
# TODO: handle the following?
# color
# comment
# drawing_sheet
# face
# maxheight
# maxlen
# name
# pngdata
# polygon
# pts

ALL_WKS_VARS = {
  "TITLE",
  "ISSUE_DATE",
  "REV",
  "COMPANY",
  "LAYER",
  "PAPER",
  "KICAD_VERSION",
}.union({f"COMMENT{i}" for i in range(10)})


@sexp.uses("ltcorner", "lbcorner", "rbcorner", "rtcorner")
def xy_from_corners(xy, gravity, corners=None):
  corners = corners or Param((0, 0, 0, 0))
  return Param(
    lambda xy, g, c: (
      c[0] + xy[0] if g[0] == "l" else c[2] - xy[0],
      c[1] + xy[1] if g[1] == "t" else c[3] - xy[1],
    ),
    xy,
    gravity,
    corners,
  )


def coord_from_corners(coord, corners, diffs):
  return xy_from_corners(
    coord.pos(diffs, relative=True),
    coord.gravity(diffs),
    corners,
  )


@sexp.handler("setup")
class Setup(sexp.SExp):
  @staticmethod
  def _to_mm(x, y):
    return (sexp.Decimal(x * 254) / 10, sexp.Decimal(y * 254) / 10)

  def is_pgone(self, context):
    pn = int(Variables.v(context).resolve(context, Variables.PAGENO) or 0)
    if pn:
      return pn == 1
    for c in reversed(context):
      if c.type == "kicad_sch" and hasattr(c, "root_path"):
        return bool(c.root_path)
    return False

  @sexp.uses(
    "paper",
    "left_margin",
    "top_margin",
    "right_margin",
    "bottom_margin",
    "portrait",
  )
  def page_corners(self, context):
    paper = ["A4"]
    for c in reversed(context):
      if c.type == "kicad_sch":
        paper = c.get("paper", paper).data
        break
    if len(paper) > 2:
      assert paper[0] == "User"
      assert len(paper) == 3
      size = tuple(paper[1:])
    else:
      size = {
        "A0": (1189, 841),
        "A1": (841, 594),
        "A2": (594, 420),
        "A3": (420, 297),
        "A4": (297, 210),
        "A5": (210, 148),
        "A": Setup._to_mm(11, 8.5),
        "B": Setup._to_mm(17, 11),
        "C": Setup._to_mm(22, 17),
        "D": Setup._to_mm(34, 22),
        "E": Setup._to_mm(44, 34),
        "USLedger": Setup._to_mm(17, 11),
        "USLegal": Setup._to_mm(14, 8.5),
        "USLetter": Setup._to_mm(11, 8.5),
      }.get(paper[0])
      assert size
      if len(paper) == 2:
        assert paper[1] == "portrait"
        size = (size[1], size[0])
    lt = (
      self.get("left_margin", default=[0])[0],
      self.get("top_margin", default=[0])[0],
    )
    rb = (
      size[0] - self.get("right_margin", default=[0])[0],
      size[1] - self.get("bottom_margin", default=[0])[0],
    )
    return lt + rb

  @property
  @sexp.uses("textsize")
  def textsize(self):
    ts = self.get("textsize")
    if ts:
      assert ts[0] == ts[1]
      return ts[0]
    return 1

  @property
  @sexp.uses("linewidth")
  def thick(self):
    return self.get("linewidth", default=["wire"])[0]

  @property
  @sexp.uses("textlinewidth")
  def textthick(self):
    return self.get("textlinewidth", default=["wire"])[0]


class Repeatable(Drawable):
  def is_pg(self):
    return False

  @sexp.uses("option", "incrx", "incry", "page1only", "notonpage1", "repeat")
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_WKS_PG if self.is_pg() else Drawable.DRAW_WKS):
      return
    config = None
    for c in reversed(context):
      if c.type == "kicad_wks":
        config = c["setup"][0]
    assert config
    is_pgone = config.is_pgone(context)
    corners = config.page_corners(context)
    params = {
      "thick": config.thick,
      "textthick": config.textthick,
      "size": config.textsize,
      "color": "SCHEMATIC_DRAWINGSHEET",
      "hidden": Param(False),
    }
    if "pos" in self:
      params["pos"] = coord_from_corners(self["pos"][0], corners, diffs)
    else:
      params["start"] = coord_from_corners(self["start"][0], corners, diffs)
      params["end"] = coord_from_corners(self["end"][0], corners, diffs)
    variables = Variables.v(context)

    def expandfunc(t):
      return variables.expand(context, t)

    # Don't render on the wrong page
    # FIXME: diffs
    option = self.get("option", default=[None])[0]
    # If pageno is unknown, assume not page 1
    if option and (option == "page1only") != is_pgone:
      # assumes no values other than page1only/notonpage1
      return
    repeat = self.get("repeat", default=[1])[0]
    incrx = self.get("incrx", default=[0])[0]
    incry = self.get("incry", default=[0])[0]
    for i in range(repeat):
      svg.gstart(hidden=params["hidden"])
      self.fillsvginst(svg, i, params, expandfunc)
      svg.gend()  # hide
      # Advance!
      for p in "pos", "start", "end":
        if p in params:
          gravity = self[p][0].gravity(diffs)
          vec = xy_from_corners((incrx, incry), gravity)
          params[p] = Param(
            lambda v, p: (v[0] + p[0], v[1] + p[1]), vec, params[p]
          )
          # Stop early if we've gone beyond the page size
          params["hidden"] = Param(
            lambda p, c: not (c[0] <= p[0] <= c[2] and c[1] <= p[1] <= c[3]),
            params[p],
            corners,
          )
          if params["hidden"].reduce(all):
            return


@sexp.handler("rect")
class Rect(Repeatable):
  def fillsvginst(self, svg, i, params, expandfunc):
    svg.rect(
      pos=params["start"],
      end=params["end"],
      color=params["color"],
      thick=params["thick"],
    )


@sexp.handler("line")
class Line(Repeatable):
  def fillsvginst(self, svg, i, params, expandfunc):
    svg.line(
      p1=params["start"],
      p2=params["end"],
      color=params["color"],
      thick=params["thick"],
    )


@sexp.handler("tbtext")
class TBText(Repeatable):
  def is_pg(self):
    return "${" in str(self[0])

  @sexp.uses("incrlabel", "rotate")
  def fillsvginst(self, svg, i, params, expandfunc):
    text = self[0]
    incr = self.get("incrlabel", default=[1])[0]
    if text == "1":
      text = f"{i * incr + 1}"
    elif text == "A":
      text = unit_to_alpha(i * incr + 1)
    else:
      text = expandfunc(text)
    lr, tb = self.justify
    bold, italic = self.style()
    svg.text(
      text=text,
      pos=params["pos"],
      textsize=self.size(params["size"]),
      textcolor=params["color"],
      bold=bold,
      italic=italic,
      justify=lr,
      vjustify=tb,
      rotate=self.get("rotate", default=[0])[0],
    )

  @sexp.uses("font", "size")
  def size(self, default):
    if "font" in self and "size" in self["font"][0]:
      size = self["font"][0]["size"][0]
      assert size[0] == size[1]
      return size[0] or default
    return default

  @sexp.uses("font", "bold", "italic")
  def style(self):
    if "font" in self:
      bold = self["font"][0].has_yes("bold")
      italic = self["font"][0].has_yes("italic")
      return (bold, italic)
    return (False, False)

  @property
  @sexp.uses("justify", "left", "middle", "right", "top", "bottom")
  def justify(self):
    lr = "left"  # unlike the rest of kicad...
    tb = "middle"
    if "justify" in self:
      lr = "middle" if "center" in self["justify"][0] else lr
      lr = "right" if "right" in self["justify"][0] else lr
      tb = "top" if "top" in self["justify"][0] else tb
      tb = "wks_bottom" if "bottom" in self["justify"][0] else tb
    return (lr, tb)


@sexp.handler("bitmap")
class Bitmap(Repeatable):
  @sexp.uses("data", "scale")
  def fillsvginst(self, svg, i, params, expandfunc):
    svg.image(
      pos=params["pos"],
      scale=self.get("scale", default=[1])[0],
      data="".join(self["data"][0].data),
    )


@sexp.handler("kicad_wks")
class KicadWks(Drawable):
  """Tracks a kicad_wks file"""

  def wks_hash(self, context):
    """calculates and returns the hash for a context."""
    # FIXME: include worksheet itself in hash?
    try:
      setup = self["setup"][0]
    except KeyError:
      setup = sexp.parse("(setup)")[0]
    return hash((setup.is_pgone(context), setup.page_corners(context)))


DEFAULT_WORKSHEET_PATH = "templates/pagelayout_default.kicad_wks"
DEFAULT_WORKSHEET = "(kicad_wks)"

# rather than supplement the variable expander with these ancient runes, do a
# pre-processing step to upgrade the file format
UPGRADE_DICT = {
  "%%": "%",
  "%C0": "${COMMENT1}",
  "%C1": "${COMMENT2}",
  "%C2": "${COMMENT3}",
  "%C3": "${COMMENT4}",
  "%C4": "${COMMENT5}",
  "%C5": "${COMMENT6}",
  "%C6": "${COMMENT7}",
  "%C7": "${COMMENT8}",
  "%C8": "${COMMENT9}",
  "%D": "${ISSUE_DATE}",
  "%F": "${FILENAME}",
  "%K": "${KICAD_VERSION}",
  "%L": "${LAYER}",
  "%N": "${##}",
  "%P": "${SHEETPATH}",
  "%R": "${REVISION}",
  "%S": "${#}",
  "%T": "${TITLE}",
  "%Y": "${COMPANY}",
  "%Z": "${PAPER}",
  "page_layout": "kicad_wks",
}


@sexp.uses("page_layout")
def kicad_wks(f, fname=None):
  if f:
    raw = f.read()
    if isinstance(raw, bytes):
      raw = raw.decode()
    data = sexp.parse(raw)
    if data[0].type == "page_layout":
      data = re.sub(
        "|".join(UPGRADE_DICT), lambda m: UPGRADE_DICT.get(m[0]), raw
      )
      data = sexp.parse(data)
    if isinstance(data[0], KicadWks):
      return data[0]
  defpath = os.path.join(os.path.dirname(__file__), DEFAULT_WORKSHEET_PATH)
  if fname == defpath or not os.path.isfile(defpath):
    return sexp.parse(DEFAULT_WORKSHEET)[0]
  return kicad_wks(open(defpath), defpath)


def main(argv):
  """USAGE: kicad_wks.py [wksfile [size]]
  Reads a kicad_wks from stdin or wksfile and renders the border at the chosen
  size (or A4 if not specified).
  """
  s = svg.Svg(theme="default")
  path = argv[1] if len(argv) > 1 else None
  w = kicad_wks(open(path) if path else sys.stdin)
  paper = argv[2] if len(argv) > 2 else "A4"
  # Placeholder title block; kicad_sch needed for variable filling
  fakepage = f'(kicad_sch (paper "{paper}"))'
  fakepage = sexp.parse(fakepage)[0]
  params = {
    "svg": s,
    "diffs": [],
    "draw": Drawable.DRAW_ALL,
    "context": (fakepage,),
  }
  w.fillsvg(**params)
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
