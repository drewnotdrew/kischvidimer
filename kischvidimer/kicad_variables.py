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
Common classes and routines for handling kicad variables
"""

import math
import random
import re
import time

from . import sexp


class Variables:
  """Tracks a variable context, which can inherit other contexts.
  There are three categories of variable references:
    1. hierarchical variables are inherited from the parent and defined by
       anything with fields (although only sheet fields have descendents)
    2. global variables are defined by symbols and are indexed by the refdes.
       note that these variables can depend on variables in that symbol's
       context, which could in turn also be global variables dependent on other
       symbols' contexts.  ${refdes:variable}
    3. title_block variables, which are inherited both by the page's contents as
       well as by the sheet that instantiated the page
  Recursive resolution needs to be aware of the context of the variable being
  resolved! Example:
    Symbol A and Symbol B both have an "address" property, but the values are
    different.
    Symbol A has a property that references symbol B's "info" property
    Symbol B's "info" property references its address property.
    When recursively resolving Symbol A's property, the address of symbol B
    should be used, not symbol A.
  To achieve this, when recursively resolving a variable, any variable
  references that get returned must be annotated with the context of the
  variable. If no annotation is present, the variable is assumed to be in its
  own context. A refdes context gets mapped to the instance where the definition
  exists.
  Variable names are case-insensitively matched, but variables can be
  case-sensitively defined, so try to match exactly first.
  Make sure to use uuid(generate=true) in case UUIDs are missing.
  It is the responsibility of the relevant objects to fill in the following
  special variables:
  Special global variables:
    ${##} -> total page count (not max pn) (kicad_pro)
    ${CURRENT_DATE} -> current date (kicad_pro)
    ${PROJECTNAME} -> name of the project (kicad_pro)
    ${FILENAME} -> file name (seems to return root file name) (kicad_sch)
    ${FILEPATH} -> full path (seems to return root file path) (kicad_sch)
  Special sheet variables:
    ${SHEETPATH} -> full page path, ending in a slash
  Special page variables:
    ${#} -> page number (kicad_pro)
  Special symbol variables: (all handled by symbol_inst)
    ${ref:DNP} -> "DNP" or ""
    ${ref:EXCLUDE_FROM_BOARD} -> "Excluded from board" or ""
    ${ref:EXCLUDE_FROM_BOM} -> "Excluded from BOM" or ""
    ${ref:EXCLUDE_FROM_SIM} -> "Excluded from simulation" or ""
    ${ref:FOOTPRINT_LIBRARY} -> footprint field (prior to colon if present)
    ${ref:FOOTPRINT_NAME} -> footprint field, after colon. blank if no colon
    ${ref:NET_CLASS(<pin_number>)} -> net class of attached net to pin
    ${ref:NET_NAME(<pin_number>)} -> connection name of net attached to pin
    ${ref:OP} -> "--"? probably something to do with simulation
    ${ref:PIN_NAME(<pin_number>)} -> name of the pin
    ${ref:SHORT_NET_NAME(<pin_number>)} -> local name of the net attached to pin
    ${ref:SYMBOL_DESCRIPTION} -> description from the library cache
    ${ref:SYMBOL_KEYWORDS} -> keywords from the library cache
    ${ref:SYMBOL_LIBRARY} -> library name
    ${ref:SYMBOL_NAME} -> symbol name
    ${ref:UNIT} -> unit LETTER
  Special net field variables: (all handled by label)
    ${CONNECTION_TYPE} -> "Input", "Output", "Bidirectional", "Tri-State",
                          "Passive"; undefined for local nets
    ${NET_CLASS} -> net class
    ${NET_NAME} -> connection name
    ${OP} -> "--"? probably something to do with simulation
    ${SHORT_NET_NAME} -> local name
  FIXME: is there *any* sane way to handle diffs?
  """

  GLOBAL = ""
  PAGENO = "#"
  PAGECOUNT = "##"
  # FIXME: handle backslash escapes better
  RE_VAR = re.compile(r"(?<!\\)\${([^}:]+:)?([^}]+)}")
  RE_EXPR = re.compile(r"(?<!\\)@{(?:" + RE_VAR.pattern + r"|[^}])*}")
  RE_IF = re.compile(r"(?<![a-zA-Z0-9_{])if(?![a-zA-Z0-9_])")
  UNITS = {
    "ps/mm": 1,
    "ps/cm": 1,
    "ps/in": 1,
    "mm": 1,
    "cm": "1/10",
    "in": "254/10",
    # '"': "254/10",  # needs special-casing
    "mil": "254/10000",
    "thou": "254/10000",
    "um": 1000,
    "deg": 1,
    "°": 1,
    "ps": 1,
    "ns": 1,
    "fs": 1,
    "f": "1e-15",
    "p": "1e-12",
    "n": "1e-9",
    "u": "1e-6",
    "m": "1e-3",
    "k": 1000,
    "K": 1000,
    "M": 1000000,
    "G": 1000000000,
    "T": 1000000000000,
    "P": 1000000000000000,
  }
  RE_NUM = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
  RE_UNITS = re.compile(
    f"({RE_NUM.pattern}" + r"\s*)(" + "|".join(UNITS) + r")(?![a-zA-Z0-9_])"
  )
  UNITS_SUB = {u: f"*{v}{'':{i}}" for i, (u, v) in enumerate(UNITS.items())}

  def __init__(self):
    # Maps a uuid to a dict of variable definitions. If a variable isn't defined
    # in a uuid's dict, go a step up the hierarchy. "" is a special context that
    # is global.
    self._contexts = {}

  def context(self):
    s = sexp.SExp.init(
      [
        sexp.Atom("~variables"),
      ]
    )
    s.variables = self
    return (s,)

  def _resolve_context(self, context):
    """Converts a context tuple, ref string, or UUID string into a UUID"""
    if not context:
      return ""
    elif isinstance(context, str):
      if len(context) == 36:  # kicad/19623
        return min(
          (c for c in self._contexts if c.endswith(context)), default=None
        )
      return context
    elements = [""]
    for c in context:
      if c.type == "path":
        elements = [c.uuid()]
      elif hasattr(c, "uuid"):
        elements.append(c.uuid(generate=True))
    return "/".join(elements)

  @staticmethod
  def v(context):
    """Finds the first variables instance in the context
    Returns a dummy class with expand/resolve if not found.
    """
    if isinstance(context, Variables):
      return context
    for c in context:
      if hasattr(c, "variables"):
        return c.variables

    class Dummy:
      def expand(self, context, text, hist=None):
        return text

      def resolve(self, context, variable, hist=None):
        return None

    return Dummy()

  def define(self, context, variable, value):
    if value is None:
      return
    value = str(value)
    context = self._resolve_context(context)
    vardict = self._contexts.setdefault(context, {})
    vardict[variable] = value
    # For case-insensitive fallback matching
    vardict.setdefault(variable.upper(), value)

  def expand(self, context, text, hist=None):
    text = Variables.RE_VAR.sub(
      lambda m: self.resolve(context, m, hist), str(text)
    )
    text = Variables.RE_EXPR.sub(lambda m: self.evaluate(context, m[0]), text)
    return text

  def resolve(self, context, variable, hist=None):
    """Variable can be x, x:y, or a match object.
    If the variable isn't found, returns None if variable was a string, or the
    full match text if the variable is a match object.
    """
    # FIXME: support querying the netlist
    hist = set() if hist is None else hist
    orig_variable = None
    orig_context_text = None
    if isinstance(variable, re.Match):
      orig_variable = variable[0]
      if variable[1]:
        context = orig_context_text = variable[1][:-1].strip()
      variable = variable[2].strip()
    elif ":" in variable:
      context, _, variable = variable.partition(":")
      context = orig_context_text = context.strip()
      variable = variable.strip()
    if variable.partition(" ")[0] in (
      "ERC_WARNING",
      "ERC_ERROR",
      "DRC_WARNING",
      "DRC_ERROR",
    ):
      return ""
    context = self._resolve_context(context)
    while True:
      hist_entry = (context, variable)
      # If we've cycled, go up a level and continue if possible
      if hist_entry not in hist:
        hist.add(hist_entry)
        vardict = self._contexts.get(context, {})
        resolved = vardict.get(variable, vardict.get(variable.upper()))
        if resolved is not None:
          expanded = self.expand(context, resolved, hist)
          # Ensure the final page list for INTERSHEET_REFS is unique and sorted
          if variable == "INTERSHEET_REFS":
            try:
              return ",".join(sorted(set(expanded.split(",")), key=int))
            except ValueError:
              return ""
          return expanded
      if not context:
        if orig_context_text:
          return f"<Unresolved: {orig_context_text}>"
        return orig_variable
      context = context.rpartition("/")[0]

  def evaluate(self, context, expr):
    """Evaluates an arbitrary string expression, including the @{}"""
    orig_expr = expr
    expr = expr[2:-1]  # remove @{}

    # Use a parser-ignored and likely unique string to tag replaces so that we
    # can undo them if it turns out they were inside a string literal.
    tag = "\t \t  \t \t"

    # FIXME: parse quote unit
    # FIXME: numbers can be added to strings (becomes concat), not vice-versa
    expr = expr.replace("^", f"{tag}**{tag}")
    expr = Variables.RE_IF.sub(f"{tag}__if__{tag}", expr)
    expr = Variables.RE_UNITS.sub(
      lambda m: f"{tag}({tag}{m[1]}{tag}{Variables.UNITS_SUB[m[2]]}){tag}", expr
    )

    g = evaluation_context()
    try:
      ret = eval(expr, g, g)
    except (NameError, SyntaxError):
      return orig_expr

    if isinstance(ret, (bool, int)):
      # Booleans are output as 1 or 0
      ret = int(ret)
    elif isinstance(ret, float):
      # Floats should avoid having ".0" if possible
      ret = f"{ret:g}"
    elif isinstance(ret, str):
      # Undo changes that still have the tagging
      ret = ret.replace(f"{tag}**{tag}", "^")
      ret = ret.replace(f"{tag}({tag}", "")
      ret = ret.replace(f"{tag}__if__{tag}", "if")
      for orig, t in Variables.UNITS_SUB.items():
        ret = ret.replace(f"{tag}{t}){tag}", orig)
    else:
      # Weird types suggest an eval issue (e.g., a function object returned)
      ret = orig_expr

    return str(ret)


# fmt: off
ESERIES_DATA = (
  (24,
    (
      100, 110, 120, 130, 150, 160, 180, 200, 220, 240, 270, 300, 330, 360, 390,
      430, 470, 510, 560, 620, 680, 750, 820, 910, 1000,
    ),
  ),
  (192,
    (
      100, 101, 102, 104, 105, 106, 107, 109, 110, 111, 113, 114, 115, 117, 118,
      120, 121, 123, 124, 126, 127, 129, 130, 132, 133, 135, 137, 138, 140, 142,
      143, 145, 147, 149, 150, 152, 154, 156, 158, 160, 162, 164, 165, 167, 169,
      172, 174, 176, 178, 180, 182, 184, 187, 189, 191, 193, 196, 198, 200, 203,
      205, 208, 210, 213, 215, 218, 221, 223, 226, 229, 232, 234, 237, 240, 243,
      246, 249, 252, 255, 258, 261, 264, 267, 271, 274, 277, 280, 284, 287, 291,
      294, 298, 301, 305, 309, 312, 316, 320, 324, 328, 332, 336, 340, 344, 348,
      352, 357, 361, 365, 370, 374, 379, 383, 388, 392, 397, 402, 407, 412, 417,
      422, 427, 432, 437, 442, 448, 453, 459, 464, 470, 475, 481, 487, 493, 499,
      505, 511, 517, 523, 530, 536, 542, 549, 556, 562, 569, 576, 583, 590, 597,
      604, 612, 619, 626, 634, 642, 649, 657, 665, 673, 681, 690, 698, 706, 715,
      723, 732, 741, 750, 759, 768, 777, 787, 796, 806, 816, 825, 835, 845, 856,
      866, 876, 887, 898, 909, 920, 931, 942, 953, 965, 976, 988, 1000,
    ),
  ),
)
# fmt: on


def eseries(mode, value, series):
  snum = series[1:].isnumeric() and int(series[1:])
  if series[0] not in "eE" or snum not in (3, 6, 12, 24, 48, 96, 192):
    raise NameError("invalid E series")
  scale = math.pow(10, int(math.log10(value)) - 2)
  value /= scale
  for base, data in ESERIES_DATA:
    if snum <= base:
      i = next(i for i, x in enumerate(data) if x > value)
      down = data[i - 1] * scale
      up = data[i] * scale
      if mode < 0 or data[i] == value:
        return down
      if mode > 0:
        return up
      return up if abs(up - value) < abs(down - value) else down
  raise RuntimeError()


def evaluation_context():
  """Defines all available symbols/functions for the string evaluator"""
  if hasattr(evaluation_context, "g"):
    return evaluation_context.g
  g = evaluation_context.g = {}

  g["abs"] = abs
  g["sqrt"] = math.sqrt
  g["pow"] = math.pow
  g["floor"] = math.floor
  g["ceil"] = math.ceil
  g["round"] = round
  g["min"] = min
  g["max"] = max
  g["sum"] = sum
  g["avg"] = lambda *a: sum(a) / len(a)
  g["shunt"] = lambda x, y: x * y / (x + y) if x + y > 0 else 0
  g["db"] = lambda x: 10 * math.log10(x)
  g["dbv"] = lambda x: 20 * math.log10(x)
  g["fromdb"] = lambda x: math.pow(10, x / 10)
  g["fromdbv"] = lambda x: math.pow(10, x / 20)

  g["enearest"] = lambda x, s="E24": eseries(0, x, s)
  g["edown"] = lambda x, s="E24": eseries(-1, x, s)
  g["eup"] = lambda x, s="E24": eseries(1, x, s)

  g["today"] = lambda: int(time.time() / (24 * 3600))
  g["now"] = lambda: int(time.time())
  g["random"] = lambda: random.uniform(0, 1)

  g["upper"] = lambda a: str(a).upper()
  g["lower"] = lambda a: str(a).lower()
  g["concat"] = lambda *a: "".join(map(str, a))
  g["beforefirst"] = lambda a, c: str(a).partition(str(c)[0])[0]
  g["beforelast"] = lambda a, c: str(a).rpartition(str(c)[0])[0]
  g["afterfirst"] = lambda a, c: str(a).partition(str(c)[0])[2]
  g["afterlast"] = lambda a, c: str(a).rpartition(str(c)[0])[2]

  g["format"] = lambda x, decimals=2: f"{float(x):.{int(decimals)}f}"
  g["fixed"] = g["format"]
  g["currency"] = lambda x, symbol="$": f"{symbol}{float(x):.2f}"

  g["weekdayname"] = lambda d: time.strftime(
    "%A", time.gmtime(float(d) * 24 * 3600)
  )

  def datestring(s):
    cjk = "年月日년월일"
    is_cjk = any(c in s for c in cjk)
    has_slash = "/" in s
    if not is_cjk and len(s) == 8:
      fmt = "%Y%d%m"
    else:
      p = [x.strip() for x in re.split(f"[-./{cjk}]", str(s), maxsplit=3)]
      s = ".".join(p)
      fmt = "%Y.%m.%d"
      if len(p) == 1:
        fmt = "%Y"
      elif len(p) == 2:
        fmt = "%Y.%m"
      elif not is_cjk and has_slash and p[0] <= 12 and p[1] <= 31:
        fmt = "%m.%d.%Y"
      elif not is_cjk and has_slash and p[0] <= 12:
        fmt = "%d.%m.%Y"
    return int(time.mktime(time.strptime(s, fmt)) / (24 * 3600))

  def dateformat(d, fmt="ISO"):
    fmt = fmt.lower()
    if fmt == "us":
      fmt = "%m/%d/%Y"
    elif fmt in ("eu", "european"):
      fmt = "%d/%m/%Y"
    elif fmt == "long":
      fmt = "%B %d, %Y"
    elif fmt == "short":
      fmt = "%b %d, %Y"
    elif fmt in ("cn", "jp", "chinese", "japanese", "中文", "日本語"):
      fmt = "%Y年%m月%d日"
    elif fmt in ("kr", "korean", "한국어"):
      fmt = "%Y년%m월%d일"
    else:
      fmt = "%Y-%m-%d"
    return time.strftime(fmt, time.gmtime(float(d) * 24 * 3600))

  g["datestring"] = datestring
  g["dateformat"] = dateformat

  g["__if__"] = lambda c, t, f: t if c else f
  g["__builtins__"] = {}

  return g
