"""SSR 응답을 읽을 수 있는 구조 다이제스트로 줄인다.

전문을 저장하면 페이지 하나가 10만자를 넘어 저장소가 부풀고, 해시로 줄이면 "무언가 달라졌다" 만 남고
무엇이 달라졌는지가 0비트가 된다 — 템플릿 공백 한 칸에 전 스냅샷이 동시에 빨개져 diff 를 못 읽는다.

그래서 회귀 판정에 필요한 축만 뽑는다. 태그 스켈레톤(깊이·태그·class·id), 표의 행 수와 첫 셀,
상태를 나르는 class 의 등장 횟수, 숫자·라벨 텍스트 앵커, 정적 자원 링크. 대규모 구조 변경이
화면을 바꿨는지는 이 축들이 잡는다.

stdlib `html.parser` 만 쓴다 — 신규 의존 0.
"""

import re
from collections import Counter
from html.parser import HTMLParser
from typing import Any, override

_STATE_CLASS = re.compile(r"^(rec-|badge-|risk-|usage-|empty-state|donut-|seg-|status-)")
_WS = re.compile(r"\s+")


class _Digester(HTMLParser):
    """태그 스트림을 훑으며 구조 축을 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skeleton: list[str] = []
        self.state_classes: Counter[str] = Counter()
        self.text_anchors: list[str] = []
        self.assets: list[str] = []
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
        if self._skip_text:
            return
        if self._in_row_first_cell:
            self._cell_buffer.append(data)
        text = _WS.sub(" ", data).strip()
        if text and (any(ch.isdigit() for ch in text) or len(text) <= 24):
            self.text_anchors.append(text[:60])


def html_digest(body: str) -> dict[str, Any]:
    """렌더된 HTML 을 구조 축 dict 로 줄인다."""
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
