import json
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pytest

_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "src/assessment_engine/web/static"
_STATIC_PREFIX = "/static/"


def _referenced_paths() -> list[tuple[str, str]]:
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
    assert _REFERENCES, "스냅샷에서 /static 참조를 못 찾았다 — digest 의 assets 축이 바뀌었다"


@pytest.mark.parametrize(("snapshot", "relative"), _REFERENCES, ids=[f"{s}:{r}" for s, r in _REFERENCES])
def test_referenced_static_asset_exists(snapshot: str, relative: str):
    assert (_STATIC_DIR / relative).is_file(), f"{snapshot} 이 참조하는 {relative} 가 없다"
