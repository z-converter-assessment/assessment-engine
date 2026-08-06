"""템플릿이 거는 정적 자원이 실제로 존재하는지 — 자산 재배치 drift 가드.

지금 이 drift 는 브라우저 콘솔 404 로만 드러난다. wheel smoke 는 `base.html` 과 `chart-utils.js`
두 개의 존재만 확인하므로, 파일 하나를 옮기고 템플릿 한 곳을 안 고치면 배포까지 통과한다.

참조 목록은 스냅샷이 이미 담고 있는 `assets` 축에서 읽는다 — 템플릿을 다시 파싱하면 스냅샷과
다른 규칙으로 두 번 세게 되고, 렌더된 적 없는 참조까지 검사 대상이 되어 두 목록이 갈린다.
"""

import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pytest

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "src/assessment_engine/web/static"
_STATIC_PREFIX = "/static/"


def _referenced_paths() -> list[tuple[str, str]]:
    """(스냅샷 이름, /static 상대 경로) — 외부 URL·data: URI 는 대상이 아니다."""
    found: set[tuple[str, str]] = set()
    for snapshot in sorted(_SNAPSHOT_DIR.glob("*.json")):
        captured = cast("dict[str, Any]", json.loads(snapshot.read_text(encoding="utf-8")))
        digest = cast("dict[str, Any]", captured.get("digest", {}))
        for asset in cast("list[str]", digest.get("assets", [])):
            _, _, url = asset.partition(":")
            path: str = urlparse(url).path
            if path.startswith(_STATIC_PREFIX):
                found.add((snapshot.stem, path.removeprefix(_STATIC_PREFIX)))
    return sorted(found)


_REFERENCES = _referenced_paths()


def test_snapshots_actually_reference_static_assets():
    """참조를 하나도 못 읽었다면 이 파일은 아무것도 검사하지 않는 상태다."""
    assert _REFERENCES, "스냅샷에서 /static 참조를 못 찾았다 — digest 의 assets 축이 바뀌었다"


@pytest.mark.parametrize(("snapshot", "relative"), _REFERENCES, ids=[f"{s}:{r}" for s, r in _REFERENCES])
def test_referenced_static_asset_exists(snapshot: str, relative: str):
    assert (_STATIC_DIR / relative).is_file(), f"{snapshot} 이 참조하는 {relative} 가 없다"
