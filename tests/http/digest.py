import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any, override

_STATE_CLASS = re.compile(r"^(rec-|badge-|risk-|usage-|empty-state|donut-|seg-|status-)")
_WS = re.compile(r"\s+")
_IMPORTMAP_URL = re.compile(r'"\s*:\s*"(/static/[^"]+)"')


class _Digester(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skeleton: list[str] = []
        self.state_classes: Counter[str] = Counter()
        self.text_anchors: list[str] = []
        self.assets: list[str] = []
        self._in_importmap = False
        self._table_stack: list[list[str]] = []
        self.tables: list[dict[str, Any]] = []
        self._in_row_first_cell = False
        self._cell_buffer: list[str] = []
        self._skip_text = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: (v or "") for k, v in attrs}
        classes = sorted(attr.get("class", "").split())
        self.skeleton.append(f"{self.depth}:{tag}:{','.join(classes)}:{attr.get('id', '')}")
        for c in classes:
            if _STATE_CLASS.match(c):
                self.state_classes[c] += 1
        if tag == "script" and attr.get("src"):
            self.assets.append(f"script:{attr['src']}")
        if tag == "script" and attr.get("type") == "importmap":
            self._in_importmap = True
        if tag == "link" and attr.get("href"):
            self.assets.append(f"link:{attr['href']}")
        if tag in {"script", "style"}:
            self._skip_text += 1
        if tag == "table":
            self._table_stack.append([])
        if tag == "tr" and self._table_stack:
            self._in_row_first_cell = True
            self._cell_buffer = []
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.depth += 1

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.depth = max(0, self.depth - 1)
        if tag in {"script", "style"}:
            self._skip_text = max(0, self._skip_text - 1)
        if tag in {"td", "th"} and self._in_row_first_cell:
            self._table_stack[-1].append(_WS.sub(" ", "".join(self._cell_buffer)).strip()[:60])
            self._in_row_first_cell = False
        if tag == "table" and self._table_stack:
            rows = self._table_stack.pop()
            self.tables.append({"rows": len(rows), "first_cells": rows})

    @override
    def handle_data(self, data: str) -> None:
        if self._in_importmap:
            self._in_importmap = False
            self.assets.extend(f"importmap:{url}" for url in sorted(_IMPORTMAP_URL.findall(data)))
            return
        if self._skip_text:
            return
        if self._in_row_first_cell:
            self._cell_buffer.append(data)
        text = _WS.sub(" ", data).strip()
        if text and (any(ch.isdigit() for ch in text) or len(text) <= 24):
            self.text_anchors.append(text[:60])


def html_digest(body: str) -> dict[str, Any]:
    parser = _Digester()
    parser.feed(body)
    parser.close()
    return {
        "skeleton": parser.skeleton,
        "state_classes": dict(sorted(parser.state_classes.items())),
        "tables": parser.tables,
        "text_anchors": parser.text_anchors,
        "assets": parser.assets,
    }
