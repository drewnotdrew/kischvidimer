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

"""
Common classes and routines for handling sexp-based KiCad modifier elements
"""

from . import sexp
from .diff import FakeDiff, Param


class HasModifiers(sexp.SExp):
  LITERAL_MAP = {}  # shouldn't have any literals

  @staticmethod
  def _wrap_svgargs_with_diff(apply, item, args, diffs, context):
    new_args = args.copy()
    item.fillsvgargs(new_args, diffs, context)
    for key, newvalue in new_args.items():
      oldvalue = args.get(key)
      if newvalue is not oldvalue:
        args[key] = Param(
          lambda a, o, n: n if a else o,
          apply,
          oldvalue,
          newvalue,
          default=args.get(key),
        )

  def fillsvgargs(self, args, diffs, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    added, removed = self.added_and_removed(
      diffs, (Modifier, HasModifiers), ModifierRoot
    )
    for item, add_c in added:
      apply = FakeDiff(add_c, new=True).param()
      self._wrap_svgargs_with_diff(apply, item, args, diffs, context)
    for item in self.data:
      if isinstance(item, (Modifier, HasModifiers)) and not isinstance(
        item, ModifierRoot
      ):
        rm_c = removed.get(id(item))
        if rm_c:
          apply = FakeDiff(rm_c, old=True).param()
          self._wrap_svgargs_with_diff(apply, item, args, diffs, context)
        else:
          item.fillsvgargs(args, diffs, context)


class ModifierRoot(HasModifiers):
  """A tag that informs not to recurse into this object from above"""


@sexp.handler("font")
class Font(HasModifiers):
  @sexp.uses("bold", "italic")
  def fillsvgargs(self, args, diffs, context):
    super().fillsvgargs(args, diffs, context)
    args["bold"] = self.has_yes("bold", diffs, args.get("bold"))
    args["italic"] = self.has_yes("italic", diffs, args.get("italic"))


@sexp.handler("effects")
class Effects(HasModifiers):
  """font effects: href, mirror, hide, font"""

  @sexp.uses("hide")
  def fillsvgargs(self, args, diffs, context):
    super().fillsvgargs(args, diffs, context)
    if self.get("hide") is not None:
      args["hidden"] = self.has_yes("hide", diffs, args.get("hidden"))


class Modifier(sexp.SExp):
  # map of literal name to arg name, if different. if None, will ignore.
  ARG_MAP = {}
  # map of values to replacement values
  VALUE_MAP = {}

  def fillsvgargs(self, args, diffs, context):
    # Grabs the literals and puts them in args
    for key in self.LITERAL_MAP:
      param = self.ARG_MAP.get(key, key)
      if param is not None:
        if self.VALUE_MAP:
          args[param] = Param(
            lambda v: self.VALUE_MAP.get(v, v),
            self.param(diffs, key),
            default=args.get(param),
          )
        else:
          args[param] = self.param(diffs, key, default=args.get(param))

  @classmethod
  def basic(cls, name, arg=False, istuple=False):
    @sexp.handler(name)
    class BasicModifier(cls):
      LITERAL_MAP = {name: (1, -1) if istuple else 1}
      ARG_MAP = {name: name if arg is False else arg}

    return BasicModifier


# margins should have 4 entries (left, top, right, bottom).
Margins = Modifier.basic("margins", istuple=True)

Face = Modifier.basic("face", None)  # TODO: support font faces
Thickness = Modifier.basic("thickness")  # TODO: properly support thick fonts
Href = Modifier.basic("href", "url")
Scale = Modifier.basic("scale")  # image scale factor


@sexp.handler("diameter")
class Diameter(Modifier):
  """diameter is like radius, but twice as large!"""

  LITERAL_MAP = {"diameter": 1}

  def fillsvgargs(self, args, diffs, context):
    p = self.param(diffs)
    # A diameter of 0 means default
    args["radius"] = Param(
      lambda d: sexp.Decimal(d) / 2 or None, p, default=args.get("radius")
    )


@sexp.handler("size")
class Size(Modifier):
  """size can be a textsize or a physical size, and can have 1 or 2 values."""

  LITERAL_MAP = {"size": (1, -1)}

  @property
  def is_textsize(self):
    return self.parent.type == "font"

  def fillsvgargs(self, args, diffs, context):
    p = self.param(diffs)
    if self.is_textsize:
      args["textsize"] = Param(lambda d: d[0], p, default=args.get("textsize"))
      args["textstretch"] = Param(
        lambda d: d[1] / d[0] if len(d) > 1 and d[0] else None,
        p,
        default=args.get("textstretch"),
      )
    else:
      args["size"] = Param(p, default=args.get("size"))

  def reparent(self, new_parent):
    super().reparent(new_parent)


@sexp.handler("color")
class Color(Modifier):
  """parameter is different depending on the context of the color."""

  LITERAL_MAP = {"color": (1, -1)}
  VALUE_MAP = {(0, 0, 0): None, (0, 0, 0, 0): None}

  def reparent(self, new_parent):
    super().reparent(new_parent)
    if new_parent.type == "font":
      self.ARG_MAP = {"color": "textcolor"}
    elif new_parent.type == "fill":
      self.ARG_MAP = {"color": "fill"}


@sexp.handler("justify")
class Justify(Modifier):
  """justify can specify either or both horizontal and vertical."""

  LITERAL_MAP = {"justify": (1, -1)}

  def param(self, diffs, key, default=None):
    assert key in ("justify", "vjustify")
    v = ("top", "bottom") if key[0] == "v" else ("left", "right")
    return Param(
      lambda j: v[0] if v[0] in j else v[1] if v[1] in j else None,
      super().param(diffs),
      default=default,
    )

  def fillsvgargs(self, args, diffs, context):
    for param in "justify", "vjustify":
      args[param] = self.param(diffs, param, default=args.get(param))


@sexp.handler("type")
class Type(Modifier):
  """parameter is different depending on the context of type."""

  LITERAL_MAP = {"type": 1}

  def reparent(self, new_parent):
    super().reparent(new_parent)
    if new_parent.type == "stroke":
      self.ARG_MAP = {"type": "pattern"}
      self.VALUE_MAP = {"default": None}
    elif new_parent.type == "fill":
      self.ARG_MAP = {"type": "fill"}
      self.VALUE_MAP = {
        "background": "device_background",
        "color": None,  # value will get filled in by Color
      }
    elif new_parent.type == "file":
      # embedded_files tag each file, e.g. (type worksheet)
      pass
    else:
      raise NotImplementedError(f"unexpected type parent {new_parent.type}")


@sexp.handler("width")
class Width(Modifier):
  """line width, actually"""

  LITERAL_MAP = {"thick": 1}
  VALUE_MAP = {0: None}


@sexp.handler("stroke")
class Stroke(HasModifiers):
  """stroke effects: width, type, color"""


@sexp.handler("fill")
class Fill(HasModifiers):
  """fill properties: type, color"""


class HasYes(sexp.SExp):
  """Marker class for easy identification."""

  LITERAL_MAP = {"yes": 1}

  @classmethod
  def handler(cls, name):
    @sexp.handler(name)
    class Wrapper(cls):
      pass

    return Wrapper


Bold = HasYes.handler("bold")
Hide = HasYes.handler("hide")
Italic = HasYes.handler("italic")
ShowName = HasYes.handler("show_name")
