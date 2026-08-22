// SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
// SPDX-FileCopyrightText: Copyright 2024 Google LLC
//   Licensed under the Apache License, Version 2.0 (the "License");
//   you may not use this file except in compliance with the License.
//   You may obtain a copy of the License at
//
//       http://www.apache.org/licenses/LICENSE-2.0
//
//   Unless required by applicable law or agreed to in writing, software
//   distributed under the License is distributed on an "AS IS" BASIS,
//   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//   See the License for the specific language governing permissions and
//   limitations under the License.
// SPDX-License-Identifier: Apache-2.0

import { pako } from "js-libraries/pako_inflate";
const uiData = {}; // diffui stub
const indexData = {}; // diffui stub
const svgData = {}; // diffui stub
export let ui = null;
let index = null;

/** Many functions return one or more "results":
result = {
  distance: NO_MATCH | int, where 0 is an exact match.
            in the case of NO_MATCH, the rest of the fields may be undefined
  type: "component" | "pin" | "net" | "bus" | "text"
  pages: list of pages that contain the result
  id: a reusable identifier for quick database access, depends on type
  prop: the name of the property that matched
  value: the value of the property that matched
  display: friendly display text for the result
  data: key/value store of data associated with the result
  target: the DOM element that initiated the lookup. Added by ::lookupElem.
          Could be an element inside of a <use>, for instance.
          This may be different from the query element, e.g. the text element if
          a tspan is queried.
  container: the DOM element representing this lookup. Added by ::lookupElem.
             This could be the <g> that includes the target
}
*/

/// PAGE FUNCTIONS
export let curPageIndex = null;
export const CUR = -1; // parameter that can replace curPageIndex
export const ALL = -2; // all pages as a parameter, when appropriate

export function selectPage(pageIndex) {
  if (pageIndex === curPageIndex) {
    return null;
  }
  curPageIndex = pageIndex;
  return getPageSvg(pageIndex);
}

export function numPages() {
  return index.pages.length;
}

export function forEachPage(callback) {
  index.pages.forEach(callback);
}

export function forEachPageByNum(callback) {
  let indices = [...Array(index.pages.length).keys()];
  indices.sort((a, b) => pageNum(a) - pageNum(b));
  indices.forEach((i) => callback(index.pages[i], i, index.pages));
}

export function pageIndexFromName(pageName) {
  // handle the case where this is called with an index
  if (typeof pageName === "number") {
    return pageName;
  }
  // handle the case where this is a page number
  if (/^\d+$/.test(pageName)) {
    let pn = parseInt(pageName);
    let pageIndex = index.pages.findIndex((p) => p.pn == pn);
    if (pageIndex !== -1) {
      return pageIndex;
    }
  }
  // Search for the pageName
  return index.pages
    .map((p) => p.name.toLowerCase())
    .indexOf(pageName.toLowerCase());
}

export function pageName(i) {
  return index.pages[i === undefined || i === CUR ? curPageIndex : i].name;
}

export function pageViewBox(i) {
  return index.pages[i === undefined || i === CUR ? curPageIndex : i].box;
}

export function pageContentBox(i) {
  return index.pages[i === undefined || i === CUR ? curPageIndex : i]
    .contentbox;
}

export function pageInstance(i) {
  return index.pages[i === undefined || i === CUR ? curPageIndex : i].inst;
}

export function pageNum(i) {
  return index.pages[i === undefined || i === CUR ? curPageIndex : i].pn;
}

/// COMPONENT FUNCTIONS
export const KEY_PAGE = "\x00"; // component pageIndex
export const KEY_PATH = "\x01"; // component path (uuid)
export const KEY_LIB_ID = "\x02"; // component library ID
export const KEY_DNP = "\x03"; // component DNP

function initComps() {
  // Create a component lookup database
  index.compsByPath = {}; // page -> path -> refdes
  for (let loc in index.comps) {
    for (let inst of index.comps[loc]) {
      let pg = compProp(inst, KEY_PAGE);
      if (index.compsByPath[pg] === undefined) {
        index.compsByPath[pg] = {};
      }
      index.compsByPath[pg][compProp(inst, KEY_PATH)] = loc;
    }
  }
}

export function lookupComp(refdesOrPath, pageIndex) {
  // Returns a result of the refdes. refdes are all uppercase
  let result = {
    type: "component",
    distance: NO_MATCH,
    pages: [],
  };
  if (!refdesOrPath) {
    return result;
  }
  let refdes = refdesOrPath.toUpperCase();
  let insts = index.comps[refdes];
  let instIndex = 0;
  if (insts === undefined) {
    // maybe a path; look it up
    refdesOrPath = refdesOrPath.toLowerCase();
    refdes = refdesByPath(refdesOrPath, pageIndex);
    if (refdes) {
      insts = index.comps[refdes];
      instIndex = insts.findIndex(
        (i) => compProp(i, KEY_PATH) === refdesOrPath,
      );
    }
  }
  if (insts && insts.length && instIndex !== -1) {
    result.distance = 0;
    result.pages = insts.map((e) => compProp(e, KEY_PAGE));
    result.pages.sort();
    result.id = refdes;
    result.prop = "Reference";
    result.value = refdes;
    result.display = refdes;
    if (insts[instIndex][KEY_DNP]) {
      result.display += " (DNP)";
    }
    result.data = insts[instIndex]; // FIXME: merge the data?
  }
  return result;
}

export function compIDs(refdesOrPath, pageIndex) {
  // Returns a list of IDs for a comp on a given page
  let result = lookupComp(refdesOrPath, pageIndex);
  if (result.distance === NO_MATCH) {
    return [];
  }
  if (pageIndex === undefined || pageIndex === CUR) {
    pageIndex = CUR;
  }
  return index.comps[result.id]
    .filter((i) => pageIndex === ALL || compProp(i, KEY_PAGE) === pageIndex)
    .map((i) => compProp(i, KEY_PATH));
}

export function searchComps(query) {
  let results = [];
  for (const [refdes, instances] of Object.entries(index.comps)) {
    // TODO: split unassigned refdes into separate results
    let result = matchData(query, instances[0]);
    if (result) {
      result.type = "component";
      result.id = refdes;
      result.display = refdes;
      if (instances[0][KEY_DNP]) {
        result.display += " (DNP)";
      }
      result.pages = instances.map((i) => compProp(i, KEY_PAGE));
      result.pages.sort();
      results.push(result);
    }
  }
  return results;
}

export function compProp(compInst, key, def) {
  if (!compInst) {
    return def;
  }
  if (key in compInst) {
    return compInst[key];
  }
  key = Object.keys(compInst).find(
    (k) => k.toLowerCase() === key.toLowerCase(),
  );
  if (key) {
    if (key === KEY_PAGE) {
      return parseInt(compInst[key]);
    }
    return compInst[key];
  }
  return def;
}

export function refdesByPath(path, pageIndex) {
  path = path.toLowerCase(); // UUIDs are all lowercase
  if (pageIndex === ALL) {
    for (let i = 0; i < numPages(); i++) {
      let refdes = refdesByPath(i, path);
      if (refdes !== null) {
        return refdes;
      }
    }
  } else {
    if (pageIndex === undefined || pageIndex === CUR) {
      pageIndex = curPageIndex;
    }
    if (index.compsByPath[pageIndex] && index.compsByPath[pageIndex][path]) {
      return index.compsByPath[pageIndex][path];
    }
  }
  return null;
}

/// NET FUNCTIONS
function initNets() {}

function netType(netID) {
  return netID in index.nets.bus ? "bus" : "net";
}

function fillNetResult(result) {
  result.type = netType(result.id);
  result.pages = [];
  // Find all the pages the net belongs to. TODO: build a lookup table?
  for (let pg in index.nets.map) {
    if (result.id in index.nets.map[pg]) {
      pg = parseInt(pg);
      result.pages.push(pg);
      // If distance wasn't set by matchData, report an exact match
      if (result.distance == NO_MATCH) {
        result.distance = 0;
      }
    }
  }
  result.pages.sort();
  if (result.type !== "bus") {
    return;
  }
  // TODO: output members
}

export function searchNets(query) {
  let results = [];
  for (const [id, name] of Object.entries(index.nets.names)) {
    let result = matchData(query, name, "name");
    if (result) {
      result.id = id;
      result.display = name;
      fillNetResult(result);
      results.push(result);
    }
  }
  return results;
}

export function lookupNet(nameOrID, pageIndex, preferType) {
  // Returns a result of the net.
  // preferType, if set, pick a certain result when an elem has multiple matches
  let result = {
    type: "net",
    distance: NO_MATCH,
    pages: [],
  };
  if (!nameOrID) {
    return result;
  }
  let elemid = null;
  // FIXME: namespace collision between global nets and IDs
  if (nameOrID in index.nets.names) {
    // Simple case: the unique id of a net
    elemid = nameOrID;
  } else {
    // an object unique ID maybe?
    // FIXME: does it make sense to support ALL?
    if (pageIndex === undefined || pageIndex === CUR) {
      pageIndex = curPageIndex;
    }
    for (const [netid, nodes] of Object.entries(
      index.nets.map[pageIndex] || {},
    )) {
      if (nodes.indexOf(nameOrID) !== -1) {
        elemid = netid;
        // if we don't have a preference, or we found our preference, stop
        if (!preferType || preferType === netType(netid)) {
          break;
        }
      }
    }
  }
  if (elemid === null) {
    // Convert a name into the unique net ID. TODO: build a lookup table?
    for (const [id, nm] of Object.entries(index.nets.names)) {
      if (nm == nameOrID) {
        elemid = id;
        break;
      }
    }
  }
  if (elemid === null) {
    // Try again, case-insensitive
    const upper = nameOrID.toUpperCase();
    for (const [id, nm] of Object.entries(index.nets.names)) {
      if (nm.toUpperCase() == upper) {
        elemid = id;
        break;
      }
    }
  }
  if (elemid) {
    result.id = elemid;
    result.value = index.nets.names[elemid];
    result.display = result.value;
    result.data = {};
    fillNetResult(result);
    result.prop = result.type;
    result.data[result.prop] = result.value;
  }
  result.pages.sort();
  return result;
}

export function netIDs(nameOrID, pageIndex) {
  // Returns a list of IDs for a net on a given page
  let result = lookupNet(nameOrID, pageIndex);
  if (result.distance === NO_MATCH) {
    return [];
  }
  // TODO: does it make sense to support ALL?
  if (pageIndex === undefined || pageIndex === CUR) {
    pageIndex = CUR;
  }
  // FIXME: handle bus membership
  return index.nets.map[pageIndex][result.id];
}

/// PIN FUNCTIONS
function initPins() {}

export function searchPins(query) {
  let results = [];
  for (const [pin, syms] of Object.entries(index.pins)) {
    let match = matchData(query, pin, "pin");
    if (match) {
      for (const [pg, refdes] of syms) {
        let result = { ...match };
        result.type = "pin";
        result.display = pin;
        result.pages = [pg];
        result.value = `${refdes}[${pin}]`;
        results.push(result);
      }
    }
  }
  return results;
}

/// TEXT FUNCTIONS
function initText() {}

export function searchText(query) {
  let results = [];
  for (let [text, pages] of Object.entries(index.text)) {
    let result = matchData(query, text, "text");
    if (result) {
      result.type = "text";
      result.display = text;
      result.pages = pages;
      result.pages.sort();
      results.push(result);
    }
  }
  return results;
}

/// DIFF FUNCTIONS
function initDiffs() {
  // Initialize empty diffs keyed by numeric page index
  forEachPage((p, i) => {
    if (!index.diffs[i]) {
      index.diffs[i] = [];
    }
  });
}

export function forEachDiff(pageIndex, callback) {
  // callback => (diffPair, pageIndex)
  if (pageIndex === ALL) {
    Object.entries(index.diffs).forEach((pg_dps) =>
      pg_dps[1].forEach((dp, i, dps) => callback(dp, pg_dps[0], i, dps)),
    );
  } else {
    if (pageIndex === CUR) {
      pageIndex = curPageIndex;
    }
    index.diffs[pageIndex].forEach((dp, i, dps) =>
      callback(dp, pageIndex, i, dps),
    );
  }
}

/// UTILITY FUNCTIONS
export function init() {
  // Load schematic data
  ui = JSON.parse(decodeData(uiData));
  index = JSON.parse(decodeData(indexData));
  initComps();
  initDiffs();
  initNets();
  initPins();
  initText();
}

export function getLibrarySvg() {
  return decodeData(svgData["library"]);
}

export function getPageSvg(pageIndex) {
  return decodeData(svgData[index.pages[pageIndex].id]);
}

export const NO_MATCH = Number.MAX_SAFE_INTEGER;
export function matchDistance(needle, haystack) {
  const uHaystack = haystack.toUpperCase();
  const uNeedle = needle.toUpperCase();
  if (uHaystack == uNeedle) {
    return 0;
  }
  if (uHaystack.indexOf(uNeedle) != -1) {
    return 1;
  }
  return NO_MATCH;
}

// FIXME: don't want this exported
export function matchData(query, data, dataName) {
  if (dataName !== undefined) {
    let dataObj = {};
    dataObj[dataName] = data;
    data = dataObj;
  }
  let result = {
    distance: NO_MATCH,
    data: data,
    value: "",
  };
  for (let [prop, val] of Object.entries(result.data)) {
    // Disregard special properties
    if (!prop || prop[0] < " ") {
      continue;
    }
    let dist = matchDistance(query, val);
    if (
      dist < result.distance ||
      (dist === result.distance && val.length < result.value.length)
    ) {
      result.distance = dist;
      result.prop = prop;
      result.value = val;
      if (dist == 0) {
        break;
      }
    }
  }
  return result.distance < NO_MATCH ? result : null;
}

function decodeData(data) {
  // Optimized base116 decoder assumes valid codepoints and full padding.
  // Gzip doesn't care about trailing null bytes, so no need to strip them.
  let code =
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!"#$%&()*+,-.:;<=>?@[]^_`{|}~ ' +
    "\x07\b\t\v\f\x0E\x0F\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1A\x1B\x1C\x1D\x1E\x1F\x7F";
  let dec = new Uint8Array(128);
  for (let i = 0; i < code.length; i++) {
    dec[code.charCodeAt(i)] = i;
  }
  let POW = [116, 116 ** 2, 116 ** 3, 116 ** 4, 116 ** 5, 116 ** 6];
  let BYTE = [2 ** 8, 2 ** 16, 2 ** 24, 2 ** 32, 2 ** 40];
  let buffer = new Uint8Array((6 * data.length) / 7);
  let j = 0;
  for (let i = 0; i < data.length; ) {
    let num =
      dec[data.charCodeAt(i++)] * POW[5] +
      dec[data.charCodeAt(i++)] * POW[4] +
      dec[data.charCodeAt(i++)] * POW[3] +
      dec[data.charCodeAt(i++)] * POW[2] +
      dec[data.charCodeAt(i++)] * POW[1] +
      dec[data.charCodeAt(i++)] * POW[0] +
      dec[data.charCodeAt(i++)];
    // Can't use bit arithmetic since Javascript will truncate to 32-bit ints
    buffer[j++] = num / BYTE[4];
    buffer[j++] = (num / BYTE[3]) % 256;
    buffer[j++] = (num / BYTE[2]) % 256;
    buffer[j++] = (num / BYTE[1]) % 256;
    buffer[j++] = (num / BYTE[0]) % 256;
    buffer[j++] = num % 256;
  }
  return pako.inflate(buffer, { to: "string" });
}
