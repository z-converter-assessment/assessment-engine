"""SQLAlchemy 선언적 매핑 베이스 — 전 ORM 모델의 공통 조상."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모델 공통 베이스. 메타데이터 수집만 하고 매핑 규약은 각 모델이 선언한다."""
