"""Query repository 패키지 — 공개 표면은 인터페이스 `QueryRepository` 하나다.

구현(`SqlQueryRepository`)과 도메인별 부분은 내보내지 않는다. 소비 규약은 `docs/reference/db/repositories.md`.
"""

from assessment_engine.db.repositories.query.repository import QueryRepository

__all__ = ["QueryRepository"]
