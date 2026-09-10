#!/usr/bin/env node
/* Find identifiers app.js uses but never declares.
 *
 * `node --check` only parses. It happily accepts a file that reads a variable
 * nobody declared, because that is a runtime error, not a syntax one -- and in
 * a browser it surfaces as a blank page and "X is not defined" in a console
 * nobody has open. Editing this file by cutting ranges out of it has twice
 * removed declarations that were still in use, so this checks for that directly.
 *
 *     node scripts/check_app.js [file ...]
 *
 * It is deliberately crude: strip comments and strings, collect everything that
 * looks like a declaration, then report identifiers that are neither declared
 * nor a known global. False positives are possible; a clean run is still the
 * cheapest guard there is against shipping a dead page.
 */
const fs = require("fs");
const path = require("path");

const KNOWN = new Set([
  // language
  "Array", "Boolean", "Date", "Error", "Function", "Infinity", "Intl", "JSON",
  "Map", "Math", "NaN", "Number", "Object", "Promise", "RegExp", "Set", "String",
  "Symbol", "TypeError", "WeakMap", "arguments", "console", "decodeURI",
  "decodeURIComponent", "encodeURI", "encodeURIComponent", "globalThis",
  "isFinite", "isNaN", "parseFloat", "parseInt", "this", "undefined",
  // browser
  "Event", "CustomEvent", "EventSource", "Element", "HTMLElement", "Image",
  "URL", "URLSearchParams", "WebSocket", "XMLHttpRequest", "cancelAnimationFrame",
  "clearInterval", "clearTimeout", "document", "fetch", "getComputedStyle",
  "localStorage", "location", "matchMedia", "navigator", "performance",
  "requestAnimationFrame", "setInterval", "setTimeout", "window", "screen",
  "history", "Path2D", "Blob", "FileReader", "Worker", "IntersectionObserver",
  "DecompressionStream", "TextDecoder", "AbortController",
  // libraries this page loads before its own script
  "L", "StaticStore", "__STORE",
  // CommonJS, for the files that are also required by the tests
  "module", "exports", "require",
]);

/* Blank out comments and string literals, keeping every newline so reported
 * line numbers still point at the real line.
 *
 * This walks the source a character at a time rather than using regexes,
 * because the two rules are mutually recursive: a // inside a string is not a
 * comment (every https:// in the file would eat the rest of its line), and an
 * apostrophe inside a comment is not a string. Only a scanner that knows which
 * state it is in gets both right.
 */
/* Whether a / at this point starts a regex or is division. The preceding
   non-space character decides it: after a value (name, number, closing bracket)
   it is division; after an operator or an opening bracket it is a regex. */
function isRegexStart(before) {
  const m = before.match(/([^\s])\s*$/);
  if (!m) return true;
  return !/[\w$)\]]/.test(m[1]);
}

function strip(src) {
  let out = "";
  let i = 0;
  const n = src.length;
  const keepNewlines = (s) => s.replace(/[^\n]/g, " ");

  while (i < n) {
    const c = src[i], d = src[i + 1];
    if (c === "/" && d === "/") {
      const end = src.indexOf("\n", i);
      const stop = end === -1 ? n : end;
      out += keepNewlines(src.slice(i, stop));
      i = stop;
    } else if (c === "/" && d === "*") {
      const end = src.indexOf("*/", i + 2);
      const stop = end === -1 ? n : end + 2;
      out += keepNewlines(src.slice(i, stop));
      i = stop;
    } else if (c === "/" && isRegexStart(out)) {
      // A regex literal can contain quotes -- /[&<>"]/g is in this very file --
      // and treating that " as a string opener desynchronises everything after
      // it. Character classes can contain an unescaped /, so track those too.
      let j = i + 1, inClass = false;
      while (j < n) {
        if (src[j] === "\\") { j += 2; continue; }
        if (src[j] === "[") inClass = true;
        else if (src[j] === "]") inClass = false;
        else if (src[j] === "/" && !inClass) { j++; break; }
        else if (src[j] === "\n") break;
        j++;
      }
      while (j < n && /[a-z]/.test(src[j])) j++;      // flags
      out += keepNewlines(src.slice(i, j));
      i = j;
    } else if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      while (j < n) {
        if (src[j] === "\\") { j += 2; continue; }
        if (src[j] === c) { j++; break; }
        j++;
      }
      out += keepNewlines(src.slice(i, j));
      i = j;
    } else {
      out += c;
      i++;
    }
  }
  return out;
}

function declarations(code) {
  const names = new Set();
  const add = (n) => n && names.add(n);

  /* var / let / const, including comma-separated lists whose initialisers
     contain parentheses: `var X = px(a), Y = py(b)` declares Y as well, and
     stopping at the first ( silently loses it. So walk to the end of the
     statement tracking bracket depth and take the names at depth zero. */
  for (const m of code.matchAll(/\b(?:var|let|const)\s+/g)) {
    let i = m.index + m[0].length, depth = 0, part = "", parts = [];
    while (i < code.length) {
      const ch = code[i];
      if ("([{".includes(ch)) depth++;
      else if (")]}".includes(ch)) { if (depth === 0) break; depth--; }
      else if (ch === ";" && depth === 0) break;
      else if (ch === "\n" && depth === 0 && /^\s*$/.test(part) === false
               && !/[,=+\-*/&|?:]\s*$/.test(part)) { parts.push(part); part = ""; break; }
      else if (ch === "," && depth === 0) { parts.push(part); part = ""; i++; continue; }
      part += ch;
      i++;
    }
    parts.push(part);
    for (const pp of parts) add((pp.trim().match(/^([A-Za-z_$][\w$]*)/) || [])[1]);
  }
  // function declarations and expressions, plus their parameters
  for (const m of code.matchAll(/\bfunction\s*([A-Za-z_$][\w$]*)?\s*\(([^)]*)\)/g)) {
    add(m[1]);
    for (const p of m[2].split(","))
      add((p.trim().match(/^([A-Za-z_$][\w$]*)/) || [])[1]);
  }
  // arrow parameters, catch bindings, for-of/in bindings
  for (const m of code.matchAll(/\(([^()]*)\)\s*=>/g))
    for (const p of m[1].split(","))
      add((p.trim().match(/^([A-Za-z_$][\w$]*)/) || [])[1]);
  for (const m of code.matchAll(/([A-Za-z_$][\w$]*)\s*=>/g)) add(m[1]);
  for (const m of code.matchAll(/\bcatch\s*\(\s*([A-Za-z_$][\w$]*)/g)) add(m[1]);
  return names;
}

function used(code) {
  const names = new Map();          // name -> first line it appears on
  const lines = code.split("\n");
  lines.forEach((line, i) => {
    // Tokenise first, then inspect the characters either side by index. Doing
    // this with lookarounds inside one regex lets the engine backtrack the
    // identifier itself -- "farm:" then matches as "far", and every name in the
    // file comes out a letter short.
    for (const m of line.matchAll(/[A-Za-z_$][\w$]*/g)) {
      const prev = line[m.index - 1];
      if (prev && /[0-9]/.test(prev)) continue;             // 1e6, 0x1f -- not a name
      const before = line.slice(0, m.index).match(/([.\w$?])\s*$/);
      if (before && before[1] === ".") continue;            // a property
      const after = line.slice(m.index + m[0].length).match(/^\s*([:(])/);
      if (after && after[1] === ":") continue;              // an object key
      if (!names.has(m[0])) names.set(m[0], i + 1);
    }
  });
  return names;
}

const KEYWORDS = new Set(("break case catch class const continue debugger default delete do else " +
  "export extends finally for function if import in instanceof let new of return static super " +
  "switch this throw try typeof var void while with yield async await get set true false null " +
  "prototype constructor").split(" "));

let bad = 0;
const files = process.argv.slice(2).length
  ? process.argv.slice(2)
  : [path.join(__dirname, "..", "site", "map", "app.js")];

for (const file of files) {
  const code = strip(fs.readFileSync(file, "utf8"));
  const declared = declarations(code);
  const missing = [];
  for (const [name, line] of used(code)) {
    if (declared.has(name) || KNOWN.has(name) || KEYWORDS.has(name)) continue;
    missing.push({ name, line });
  }
  const rel = path.relative(process.cwd(), file);
  if (missing.length) {
    bad += missing.length;
    console.log(`${rel}: ${missing.length} identifier(s) used but never declared`);
    for (const m of missing) console.log(`  line ${m.line}: ${m.name}`);
  } else {
    console.log(`${rel}: ok — every identifier is declared or a known global`);
  }
}
process.exit(bad ? 1 : 0);
