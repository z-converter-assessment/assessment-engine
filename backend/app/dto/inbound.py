from pydantic import BaseModel


class DiskInput(BaseModel):
    name: str
    size: str


class IpInput(BaseModel):
    internal: list[str]
    external: list[str]


class ServerMetricInput(BaseModel):
    hostname: str
    nproc: str
    mem_total_mb: int
    disks: list[DiskInput]
    ip: IpInput