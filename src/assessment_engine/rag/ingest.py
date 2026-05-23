"""RAG 도메인 지식 ingest CLI (ADR 0024 결정 8).

사용:
    python -m assessment_engine.rag.ingest <file.md>

흐름:
1. 파일 읽기 (MD / Text 만 — PDF 는 외부 도구 pdftotext/pandoc 으로 사전 변환)
2. recursive_split — 단락 우선 분할 (chunk 500 token + overlap 50)
3. embedding 모델 batch 호출 (mxbai-embed-large default, 1024d)
4. rag_documents UPSERT — source_id = file_path + chunk_index, ON CONFLICT DO UPDATE
5. HNSW 인덱스 자동 갱신 (pgvector)

본 CLI 는 EMBEDDING_PROVIDER · EMBEDDING_MODEL · EMBEDDING_DIMENSION 환경변수 활용. mock provider 시
deterministic random vector — 본 phase 동작 검증 (실제 의미 유사도 X).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from assessment_engine.db.session import AsyncSessionLocal
from assessment_engine.diagnostic.settings import diagnostic_settings
from assessment_engine.log_config import setup_logging
from assessment_engine.rag.embedding.base import BaseEmbeddingClient

# chunk 본문 단위 = 대략 token (영어 ~ 4 char/token 가정). 단순 char 기준 split 후 token-based 정밀화는 후순위.
DEFAULT_CHUNK_CHARS = 2000  # 약 500 token 가정 (영어 4 char/token)
DEFAULT_OVERLAP_CHARS = 200  # 약 50 token


def recursive_split(
    text_body: str,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """단락 우선 -> 문장 -> char 단위 cap recursive splitter.

    1. 빈 줄 (`\\n\\n`) 으로 단락 분할
    2. 각 단락이 chunk_chars 초과면 sentence 분할 (`. ` 기준)
    3. sentence 도 초과면 char 단위 cap
    4. chunk 사이 overlap_chars 만큼 이전 chunk 꼬리 prepend
    """
    if not text_body.strip():
        return []

    paragraphs = [p.strip() for p in text_body.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_chars:
            # 단락 자체가 크면 sentence 분할
            sentences = paragraph.replace("\n", " ").split(". ")
            for sentence in sentences:
                # sentence 도 크면 char cap
                if len(sentence) > chunk_chars:
                    for i in range(0, len(sentence), chunk_chars):
                        chunks.append(sentence[i : i + chunk_chars])
                else:
                    if len(buffer) + len(sentence) + 2 > chunk_chars:
                        if buffer:
                            chunks.append(buffer.strip())
                        buffer = sentence + ". "
                    else:
                        buffer += sentence + ". "
        else:
            if len(buffer) + len(paragraph) + 2 > chunk_chars:
                if buffer:
                    chunks.append(buffer.strip())
                buffer = paragraph + "\n\n"
            else:
                buffer += paragraph + "\n\n"
    if buffer.strip():
        chunks.append(buffer.strip())

    # overlap 처리 — 본 chunk 머리에 이전 chunk 꼬리 prepend
    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks
    out: list[str] = [chunks[0]]
    for prev_idx, current in enumerate(chunks[1:]):
        prev = chunks[prev_idx]
        overlap = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
        out.append(overlap + "\n\n" + current)
    return out


def _build_embedding_client() -> BaseEmbeddingClient:
    """EMBEDDING_PROVIDER 분기 — diagnostic worker main 과 동일 패턴."""
    provider = diagnostic_settings.embedding_provider
    if provider == "mock":
        from assessment_engine.rag.embedding.mock import MockEmbeddingClient

        return MockEmbeddingClient(dimension=diagnostic_settings.embedding_dimension)
    if provider == "ollama":
        from assessment_engine.rag.embedding.ollama import OllamaEmbeddingClient

        return OllamaEmbeddingClient(
            base_url=diagnostic_settings.ollama_base_url,
            model=diagnostic_settings.embedding_model,
            timeout_seconds=diagnostic_settings.embedding_timeout_seconds,
        )
    raise ValueError(f"unknown EMBEDDING_PROVIDER: {provider}")


async def ingest_file(file_path: Path, source_type: str = "domain_knowledge") -> int:
    """파일 1건 ingest. 반환: upsert 된 chunk row 수."""
    if not file_path.is_file():
        raise FileNotFoundError(f"file not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise ValueError(
            f"unsupported file type: {suffix}. PDF/DOC 등은 외부 도구 (pdftotext/pandoc) 로 MD 변환 후 ingest."
        )

    raw_text = file_path.read_text(encoding="utf-8")
    chunks = recursive_split(raw_text)
    if not chunks:
        logger.warning("no chunks produced file={}", file_path)
        return 0

    embedding_client = _build_embedding_client()
    logger.info(
        "ingesting file={} chunks={} provider={} model={}",
        file_path,
        len(chunks),
        diagnostic_settings.embedding_provider,
        diagnostic_settings.embedding_model,
    )

    vectors = await embedding_client.embed(chunks)
    if len(vectors) != len(chunks):
        raise ValueError(f"embedding count mismatch: chunks={len(chunks)} vectors={len(vectors)}")

    upsert_sql = text("""
        INSERT INTO rag_documents (source_type, source_id, content, metadata, embedding, created_at, updated_at)
        VALUES (:source_type, :source_id, :content, CAST(:metadata AS jsonb), CAST(:embedding AS vector), now(), now())
        ON CONFLICT (source_id) DO UPDATE SET
            source_type = EXCLUDED.source_type,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            embedding = EXCLUDED.embedding,
            updated_at = now()
    """)

    file_str = str(file_path.resolve())
    rows_processed = 0
    async with AsyncSessionLocal() as session:
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            source_id = f"{file_str}#{index}"
            vector_literal = "[" + ",".join(f"{x:.8f}" for x in vector) + "]"
            metadata_json = json.dumps(
                {
                    "source": file_path.name,
                    "chunk_index": index,
                    "title": file_path.stem,
                }
            )
            await session.execute(
                upsert_sql,
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "content": chunk,
                    "metadata": metadata_json,
                    "embedding": vector_literal,
                },
            )
            rows_processed += 1
        await session.commit()
    logger.info("ingest completed file={} rows={}", file_path, rows_processed)
    return rows_processed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAG 도메인 지식 ingest CLI (ADR 0024). MD/Text 파일 1건 -> chunk + embedding + UPSERT.",
    )
    parser.add_argument("file", type=Path, help="ingest 대상 파일 (MD 또는 Text)")
    parser.add_argument(
        "--source-type",
        default="domain_knowledge",
        choices=["domain_knowledge", "operation_note"],
        help="rag_documents.source_type (default: domain_knowledge). operation_note 는 운영 노트 phase 도입 시 활용.",
    )
    return parser.parse_args()


async def _main() -> int:
    setup_logging(diagnostic_settings.log_format)
    args = _parse_args()
    try:
        rows = await ingest_file(args.file, source_type=args.source_type)
    except (FileNotFoundError, ValueError) as e:
        logger.error("ingest failed: {}", e)
        return 1
    logger.info("ingest summary file={} rows={}", args.file, rows)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
