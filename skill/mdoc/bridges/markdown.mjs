import fs from "node:fs";
import MarkdownIt from "markdown-it";
import { applyFixes, getVersion } from "markdownlint-cli2/markdownlint";
import { lint } from "markdownlint-cli2/markdownlint/promise";

const HONKIT_SLUG_SYMBOLS = new Set([...`[]!"'#$%&()*+,./:;<=>?@^\`{|}~©∑®†“”‘’∂ƒ™℠…œŒ˚ºª•∆∞♥`]);
function honkitSlug(value) {
  const slug = [...value].filter((character) => !HONKIT_SLUG_SYMBOLS.has(character)).join("").replaceAll(" ", "-").toLowerCase();
  return slug.startsWith("-") ? slug.slice(1) : slug;
}

const request = JSON.parse(fs.readFileSync(0, "utf8"));
if (request.action === "versions") {
  const packageVersion = JSON.parse(fs.readFileSync(new URL("../node_modules/markdown-it/package.json", import.meta.url), "utf8")).version;
  process.stdout.write(JSON.stringify({ markdownlint: getVersion(), markdownIt: packageVersion }));
} else if (request.action === "lint") {
  const results = await lint({ files: request.files, config: request.config, noInlineConfig: true });
  process.stdout.write(JSON.stringify(results));
} else if (request.action === "fix-content") {
  const results = await lint({ strings: { content: request.content }, config: request.config, noInlineConfig: true });
  const errors = results.content ?? [];
  const allowed = new Set(request.rules ?? []);
  const ignored = new Set((request.ignored ?? []).map((item) => `${item.rule}:${item.line}:${item.column}`));
  const applicable = errors.filter((error) => error.fixInfo && allowed.has(error.ruleNames[0]) && !ignored.has(`${error.ruleNames[0]}:${error.lineNumber}:${error.errorRange?.[0] ?? 1}`));
  process.stdout.write(JSON.stringify({ content: applyFixes(request.content, applicable), errors, applied: applicable.map((error) => ({ rule: error.ruleNames[0], line: error.lineNumber, column: error.errorRange?.[0] ?? 1 })) }));
} else if (request.action === "parse") {
  const parser = new MarkdownIt({ html: true, linkify: false, typographer: false });
  const output = {};
  for (const file of request.files) {
    const source = fs.readFileSync(file, "utf8");
    output[file] = parser.parse(source, {}).map((token) => ({
      type: token.type,
      tag: token.tag,
      nesting: token.nesting,
      level: token.level,
      map: token.map,
      content: token.content,
      attrs: token.attrs,
      children: token.children?.map((child) => ({ type: child.type, content: child.content, attrs: child.attrs, markup: child.markup })) ?? null
    }));
  }
  process.stdout.write(JSON.stringify(output));
} else if (request.action === "detect-english") {
  const parser = new MarkdownIt({ html: true, linkify: false, typographer: false });
  const japanese = /[\u3400-\u9fff\u3040-\u30ff]/u;
  const output = [];
  for (const file of request.files) {
    const source = fs.readFileSync(file, "utf8").split(/\r?\n/u).slice(0, request.scanLines).join("\n");
    const tokens = parser.parse(source, {});
    const visible = tokens.flatMap((token) => token.children ?? []).filter((child) => child.type === "text").map((child) => child.content).join("") + tokens.filter((token) => ["html_block", "html_inline"].includes(token.type)).map((token) => token.content.replace(/<[^>]*>/gu, "")).join("");
    if (!japanese.test(visible)) output.push(file);
  }
  process.stdout.write(JSON.stringify(output));
} else if (request.action === "blocks") {
  const parser = new MarkdownIt({ html: true, linkify: false, typographer: false });
  const source = request.content ?? fs.readFileSync(request.file, "utf8");
  const lines = source.split(/\n/u);
  const offsets = [0];
  for (const line of lines.slice(0, -1)) offsets.push(offsets.at(-1) + line.length + 1);
  const blocks = [];
  for (const token of parser.parse(source, {})) {
    if (!token.map || token.level !== 0 || !(token.nesting === 1 || ["fence", "code_block", "html_block", "hr"].includes(token.type))) continue;
    const [startLine, endLine] = token.map;
    const from = offsets[startLine] ?? source.length;
    const to = endLine < offsets.length ? offsets[endLine] - 1 : source.length;
    const text = source.slice(from, to);
    const images = [...text.matchAll(/!?\[[^\]]*\]\([^)]+\)|<img\b[^>]*>/giu)].map((match) => match[0]);
    const links = [...text.matchAll(/(?<!!)\[[^\]]*\]\([^)]+\)/gu)].map((match) => match[0]);
    blocks.push({ index: blocks.length, type: token.type.replace(/_open$/u, ""), from, to, start_line: startLine + 1, end_line: endLine, text, html: parser.render(text), structure: { images: images.length, links: links.length, fences: (text.match(/^```|^~~~/gmu) ?? []).length, html: (text.match(/<[^>]+>/gu) ?? []).length } });
  }
  process.stdout.write(JSON.stringify({ blocks }));
} else if (request.action === "summary") {
  const parser = new MarkdownIt({ html: true, linkify: false, typographer: false });
  const source = fs.readFileSync(request.file, "utf8");
  const entries = [];
  for (const token of parser.parse(source, {})) {
    if (token.type !== "inline" || !token.children) continue;
    const link = token.children.find((child) => child.type === "link_open");
    if (!link) continue;
    const href = link.attrGet("href");
    if (href) {
      const start = token.children.indexOf(link);
      const end = token.children.findIndex((child, index) => index > start && child.type === "link_close");
      const title = token.children.slice(start + 1, end < 0 ? undefined : end).filter((child) => ["text", "code_inline", "image"].includes(child.type)).map((child) => child.content).join("");
      entries.push({ depth: Math.max(0, Math.floor((token.level - 2) / 2)), href, title, line: (token.map?.[0] ?? 0) + 1 });
    }
  }
  process.stdout.write(JSON.stringify(entries));
} else if (request.action === "render") {
  const parser = new MarkdownIt({ html: true, linkify: false, typographer: false });
  const source = request.content ?? fs.readFileSync(request.file, "utf8");
  const tokens = parser.parse(source, {});
  const lines = [];
  const slugs = new Map();
  for (const [index, token] of tokens.entries()) {
    if (token.map && (token.nesting === 1 || ["fence", "code_block", "html_block"].includes(token.type))) {
      token.attrSet("data-source-line", String(token.map[0] + 1));
      lines.push(token.map[0] + 1);
    }
    if (token.type === "heading_open" && tokens[index + 1]?.type === "inline") {
      const text = (tokens[index + 1].children ?? []).filter((child) => ["text", "code_inline", "image"].includes(child.type)).map((child) => child.content).join("");
      const base = honkitSlug(text);
      const occurrence = slugs.get(base) ?? 0;
      slugs.set(base, occurrence + 1);
      token.attrSet("id", occurrence ? `${base}-${occurrence}` : base);
    }
  }
  process.stdout.write(JSON.stringify({ html: parser.renderer.render(tokens, parser.options, {}), lines }));
} else {
  throw new Error(`Unsupported action: ${request.action}`);
}
