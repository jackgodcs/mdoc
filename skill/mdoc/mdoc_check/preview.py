from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .checkers import BRIDGE, NODE, _run


ALLOWED = {"a", "blockquote", "br", "code", "div", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "img", "li", "ol", "p", "pre", "span", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul"}
VOID = {"br", "hr", "img"}
STYLE_VALUES = {
    "display": re.compile(r"^(?:block|inline|inline-block|flex)$"),
    "gap": re.compile(r"^\d+(?:\.\d+)?(?:px|em|rem|%)$"),
    "justify-content": re.compile(r"^(?:start|center|end|space-between|space-around)$"),
    "margin": re.compile(r"^(?:0|auto|\d+(?:\.\d+)?(?:px|em|rem|%))(?:\s+(?:0|auto|\d+(?:\.\d+)?(?:px|em|rem|%))){0,3}$"),
    "text-align": re.compile(r"^(?:left|center|right)$"),
    "vertical-align": re.compile(r"^(?:baseline|middle|top|bottom)$"),
    "width": re.compile(r"^(?:auto|\d+(?:\.\d+)?(?:px|em|rem|%))$"),
}


def safe_style(value: str) -> str:
    declarations = []
    for declaration in value.split(";"):
        if ":" not in declaration:
            continue
        name, raw = (part.strip().casefold() for part in declaration.split(":", 1))
        if name in STYLE_VALUES and STYLE_VALUES[name].fullmatch(raw):
            declarations.append(f"{name}:{raw}")
    return "; ".join(declarations)


class Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.output = []
        self.blocked = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED:
            self.blocked += 1
            return
        safe = []
        for name, value in attrs:
            if not value or name.lower().startswith("on"):
                continue
            if name == "style":
                value = safe_style(value)
                if not value:
                    self.blocked += 1
                    continue
            elif name == "start":
                if tag != "ol" or not value.isdigit() or int(value) < 1:
                    self.blocked += 1
                    continue
            elif name not in {"href", "src", "alt", "title", "class", "id", "width", "height", "align", "data-source-line"}:
                continue
            parsed = urlsplit(value)
            if name in {"href", "src"} and (parsed.scheme not in {"", "http", "https"} or value.lower().startswith("javascript:")):
                self.blocked += 1
                continue
            safe.append(f' {name}="{html.escape(value, quote=True)}"')
        self.output.append(f"<{tag}{''.join(safe)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED and tag not in VOID:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(html.escape(data))

    def handle_entityref(self, name: str) -> None:
        self.output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.output.append(f"&#{name};")


def render(path: Path) -> dict:
    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "render", "file": str(path.resolve())})).stdout)
    return _sanitize(raw)


def render_text(content: str) -> dict:
    raw = json.loads(_run([str(NODE), str(BRIDGE)], input_text=json.dumps({"action": "render", "content": content})).stdout)
    return _sanitize(raw)


def _sanitize(raw: dict) -> dict:
    sanitizer = Sanitizer(); sanitizer.feed(raw["html"])
    return {"html": "".join(sanitizer.output), "filtered": sanitizer.blocked, "lines": raw.get("lines", [])}
