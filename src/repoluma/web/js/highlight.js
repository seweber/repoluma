// Tiny, dependency-free tokenizer for readable code panes. It is deliberately
// approximate (line-based, no multi-line state) and only ever produces text nodes.

const KEYWORDS = {
  py: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self",
  js: "abstract any as async await boolean break case catch class const constructor continue debugger declare default delete do else enum export extends false finally for from function get if implements import in instanceof interface keyof let new null number of private protected public readonly return set static string super switch this throw true try type typeof undefined var void while with yield",
  go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false",
  rs: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
  generic: "if else for while return class function def import from const let var true false null None struct enum fn pub",
};
const EXT = {
  py: "py", pyi: "py", js: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js", jsx: "js",
  java: "js", kt: "js", cs: "js", c: "js", h: "js", cpp: "js", hpp: "js", cc: "js", swift: "js",
  go: "go", rs: "rs", rb: "generic", sh: "generic", bash: "generic", toml: "generic",
  yaml: "generic", yml: "generic",
};
const HASH_COMMENT = new Set(["py", "pyi", "rb", "sh", "bash", "toml", "yaml", "yml", "r", "pl"]);
const PLAIN = new Set(["md", "markdown", "txt", "rst", "json", "csv", "html", "xml"]);
const cache = new Map();

export function languageOf(path) {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return { ext, kw: EXT[ext] || "generic", hash: HASH_COMMENT.has(ext), plain: PLAIN.has(ext) };
}

function keywordSet(name) {
  if (!cache.has(name)) cache.set(name, new Set(KEYWORDS[name].split(" ")));
  return cache.get(name);
}

/** Split a line into [className, text] tokens. */
export function tokenize(line, lang) {
  if (lang.plain || line.length > 2000) return [["", line]];
  const kws = keywordSet(lang.kw);
  const out = [];
  let i = 0;
  const n = line.length;
  let prevWord = "";
  while (i < n) {
    const ch = line[i];
    const rest = line.slice(i);
    if ((lang.hash && ch === "#") || (!lang.hash && rest.startsWith("//"))) {
      out.push(["c", rest]);
      break;
    }
    if (!lang.hash && rest.startsWith("/*")) {
      const end = line.indexOf("*/", i + 2);
      const stop = end < 0 ? n : end + 2;
      out.push(["c", line.slice(i, stop)]);
      i = stop;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      let j = i + 1;
      while (j < n && line[j] !== ch) j += line[j] === "\\" ? 2 : 1;
      out.push(["s", line.slice(i, Math.min(n, j + 1))]);
      i = j + 1;
      continue;
    }
    if (/[0-9]/.test(ch)) {
      const m = /^[0-9][0-9_a-fA-FxX.]*/.exec(rest);
      out.push(["n", m[0]]);
      i += m[0].length;
      continue;
    }
    if (/[A-Za-z_$@]/.test(ch)) {
      const m = /^[@A-Za-z_$][\w$]*/.exec(rest);
      const word = m[0];
      let cls = "";
      if (kws.has(word)) cls = "k";
      else if (["def", "class", "function", "fn", "func", "interface", "struct", "type", "enum"]
        .includes(prevWord)) cls = "f";
      else if (word.startsWith("@")) cls = "d";
      out.push([cls, word]);
      prevWord = word;
      i += word.length;
      continue;
    }
    const m = /^[^A-Za-z_$@0-9"'`#/]+|^./.exec(rest);
    out.push(["", m[0]]);
    if (m[0].trim()) prevWord = "";
    i += m[0].length;
  }
  return out;
}
