"""wire·JSONB 원본 JSON 값의 타입 별칭.

에이전트가 보내고 JSONB 컬럼에 그대로 실리는 객체는 키·값이 계약으로만 정해진다. 모델로 좁히면
계약 밖 필드가 도착했을 때 통과시키라는 규약과 어긋나므로 원본은 열린 채로 두고, 읽는 쪽이 필요한
축만 좁혀 쓴다. 계약은 docs/reference/contracts/agent-data.md 가 기준.
"""

from typing import Any

# JSON object 하나 — wire payload 또는 JSONB 컬럼 원본.
JsonObject = dict[str, Any]
