"""ZDM 본체 패키지 메타데이터 (sha256·size_bytes) 동적 조회.

흐름:
  1. HEAD `http://{zdm_host}{zdm_package_path}` — ETag + Content-Length 추출
  2. Redis cache key = (host, etag) → hit 이면 cached sha256 반환
  3. miss 이면 GET stream + sha256 계산 + Redis set + 반환

설계:
  - ZDM 패키지가 자주 안 바뀜 + ETag 가 invalidation 키라 cache TTL 길게 (6h default)
  - fail-close — meta fetch 실패 시 publish 차단 (`ZdmPackageMetaError`)
  - HEAD Content-Length 와 GET 실측 byte count 일치 검증 (ZDM 측 정합성 보장)
  - Redis 자체는 fail-open (#C3) — Redis 장애 시 매번 GET full 로 fallback

인터페이스(`BaseZdmPackageResolver`) 와 구체(`HttpZdmPackageResolver`) 분리 — #F4.
"""
import hashlib
from typing import Protocol

import httpx
from loguru import logger
from redis.asyncio import Redis

from assessment_engine.db.redis import safe_get, safe_set
from assessment_engine.web.settings import web_settings


class ZdmPackageMetaError(Exception):
    """router 가 HTTPException(503) 로 변환 — ZDM 메타 조회 실패."""


class BaseZdmPackageResolver(Protocol):
    async def resolve(self, zdm_host: str) -> tuple[str, int]:
        """ZDM 패키지의 (sha256_hex, size_bytes) 반환. 실패 시 raise."""


class HttpZdmPackageResolver:
    def __init__(self, http_client: httpx.AsyncClient, redis: Redis) -> None:
        self.http = http_client
        self.redis = redis

    async def resolve(self, zdm_host: str) -> tuple[str, int]:
        url = f"http://{zdm_host}{web_settings.zdm_package_path}"

        # 1. HEAD — ETag + Content-Length
        try:
            head_resp = await self.http.head(url)
        except httpx.HTTPError as e:
            raise ZdmPackageMetaError(f"HEAD failed: {type(e).__name__}: {e}") from e
        if head_resp.status_code != 200:
            raise ZdmPackageMetaError(f"HEAD status={head_resp.status_code}")
        content_length_raw = head_resp.headers.get("Content-Length")
        if content_length_raw is None:
            raise ZdmPackageMetaError("HEAD missing Content-Length")
        try:
            size_bytes = int(content_length_raw)
        except ValueError as e:
            raise ZdmPackageMetaError(f"HEAD Content-Length not int: {content_length_raw!r}") from e
        if size_bytes <= 0:
            raise ZdmPackageMetaError(f"HEAD Content-Length non-positive: {size_bytes}")

        # ETag 우선, 없으면 Last-Modified fallback. 둘 다 없으면 cache 키 안정성 깨지지만
        # 그 경우라도 매 publish 마다 fresh GET 으로 sha256 산출 → 동작은 정확.
        etag = head_resp.headers.get("ETag") or head_resp.headers.get("Last-Modified") or ""
        cache_key = web_settings.redis_key_zdm_package_sha256.format(zdm_host, etag) if etag else ""

        # 2. cache hit?
        if cache_key:
            cached = await safe_get(self.redis, cache_key)
            if cached:
                logger.info("zdm package meta cache hit host={} etag={}", zdm_host, etag)
                return cached, size_bytes

        # 3. miss — GET stream + sha256
        sha = hashlib.sha256()
        bytes_read = 0
        try:
            async with self.http.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise ZdmPackageMetaError(f"GET status={resp.status_code}")
                async for chunk in resp.aiter_bytes():
                    sha.update(chunk)
                    bytes_read += len(chunk)
        except httpx.HTTPError as e:
            raise ZdmPackageMetaError(f"GET failed: {type(e).__name__}: {e}") from e

        if bytes_read != size_bytes:
            raise ZdmPackageMetaError(
                f"size mismatch: HEAD={size_bytes} GET={bytes_read} — ZDM 측 정합성 깨짐"
            )

        sha256_hex = sha.hexdigest()
        logger.info(
            "zdm package meta computed host={} etag={} sha256={} size={}",
            zdm_host, etag, sha256_hex[:16] + "...", size_bytes,
        )

        # 4. cache set (fail-open — Redis 장애 시 다음 publish 에서 다시 계산)
        if cache_key:
            await safe_set(self.redis, cache_key, sha256_hex, ex=web_settings.redis_ttl_zdm_package_sha256)

        return sha256_hex, size_bytes
