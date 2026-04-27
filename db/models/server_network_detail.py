from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base


class ServerNetworkDetail(Base):
    __tablename__ = "server_network_detail"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)