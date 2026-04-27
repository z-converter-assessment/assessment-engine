from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerStorageDetail(Base):
    __tablename__ = "server_storage_detail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)