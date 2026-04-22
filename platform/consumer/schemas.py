from ipaddress import IPv4Address

from pydantic import BaseModel, Field


class DiskInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    size: str = Field(pattern=r'^\d+[GMB]$')


class IpInput(BaseModel):
    internal: list[IPv4Address]
    external: list[IPv4Address]


class ServerMetricInput(BaseModel):
    hostname: str = Field(min_length=1, max_length=255)
    nproc: int = Field(gt=0)
    mem_total_mb: int = Field(gt=0)
    disks: list[DiskInput]
    ip: IpInput