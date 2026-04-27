from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerMetrics(Base):
    __tablename__ = "server_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)