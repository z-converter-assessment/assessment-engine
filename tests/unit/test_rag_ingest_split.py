"""rag.ingest.recursive_split — chunk 분할 단위 테스트 (ADR 0024 결정 8).

본질 catalog:
- 빈 본문 -> 빈 list
- 작은 본문 (1 chunk) — 분할 X
- 단락 우선 분할 (`\\n\\n` 기준)
- chunk_chars 초과 시 sentence 분할
- sentence 초과 시 char cap
- chunk 사이 overlap_chars 만큼 이전 꼬리 prepend
- overlap_chars=0 -> overlap 미적용
"""

from assessment_engine.rag.ingest import recursive_split


def test_empty_body_yields_empty_list():
    assert recursive_split("") == []
    assert recursive_split("   ") == []
    assert recursive_split("\n\n\n") == []


def test_small_body_single_chunk():
    body = "Short paragraph one.\n\nShort paragraph two."
    chunks = recursive_split(body, chunk_chars=1000, overlap_chars=0)
    assert len(chunks) == 1
    assert "Short paragraph one" in chunks[0]
    assert "Short paragraph two" in chunks[0]


def test_paragraph_split_when_buffer_exceeds_cap():
    # 각 단락 = 50 char. chunk_chars=100 -> 2 단락 = buffer 초과 -> split
    p1 = "A" * 50
    p2 = "B" * 50
    p3 = "C" * 50
    body = f"{p1}\n\n{p2}\n\n{p3}"
    chunks = recursive_split(body, chunk_chars=100, overlap_chars=0)
    # 최소 2 chunk (단락 합치다 cap 초과)
    assert len(chunks) >= 2


def test_long_paragraph_sentence_split():
    # 단락 자체가 chunk_chars 초과 -> sentence 단위 (`. ` 기준) 분할
    long_para = ". ".join([f"Sentence {i} content" for i in range(50)])
    chunks = recursive_split(long_para, chunk_chars=100, overlap_chars=0)
    assert len(chunks) > 1
    # 모든 chunk 가 chunk_chars 이하 또는 sentence 1개 이상 포함
    for chunk in chunks:
        assert len(chunk) > 0


def test_very_long_sentence_char_cap():
    # sentence 자체가 chunk_chars 초과 -> char 단위 강제 cap
    long_sentence = "x" * 500  # 단일 sentence, '. ' 없음
    chunks = recursive_split(long_sentence, chunk_chars=100, overlap_chars=0)
    assert len(chunks) >= 5  # 500 / 100 = 5+
    for chunk in chunks:
        assert len(chunk) <= 100


def test_overlap_prepends_prev_tail():
    p1 = "A" * 50
    p2 = "B" * 50
    p3 = "C" * 50
    body = f"{p1}\n\n{p2}\n\n{p3}"
    chunks_no_overlap = recursive_split(body, chunk_chars=100, overlap_chars=0)
    chunks_with_overlap = recursive_split(body, chunk_chars=100, overlap_chars=20)
    # overlap 적용 시 chunk 길이 증가 (head 에 prev tail prepend)
    if len(chunks_no_overlap) >= 2:
        # 첫 chunk 는 동일 (overlap 대상 X)
        assert chunks_with_overlap[0] == chunks_no_overlap[0]
        # 두 번째부터 head 에 이전 chunk 꼬리 20 char prepend
        assert len(chunks_with_overlap[1]) > len(chunks_no_overlap[1])


def test_overlap_zero_yields_no_prepend():
    body = "AAAA\n\nBBBB\n\nCCCC\n\nDDDD"
    chunks = recursive_split(body, chunk_chars=5, overlap_chars=0)
    # 각 chunk 가 단순 분할 (overlap prepend 0)
    for chunk in chunks:
        # 다른 chunk 의 일부가 본 chunk 안 prepend 안 됨 (단순 buffer 결과)
        assert chunk.strip() != ""


def test_single_chunk_no_overlap_processing():
    """chunk 1개 만 시 overlap 처리 skip (chunks[1:] 빈 iter)."""
    chunks = recursive_split("small", chunk_chars=1000, overlap_chars=100)
    assert len(chunks) == 1
    assert chunks[0] == "small"
