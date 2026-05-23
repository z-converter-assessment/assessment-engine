from abc import ABC, abstractmethod


class BaseLlmClient(ABC):
    """LLM 클라이언트 추상 — composition root(워커 main)에서 구체 주입 (F4).

    ADR 0004 + 0025: 단일 provider (ollama 로컬 무료 LLM). 과금 발생 외부 유료 API 호출 금지 (정책).
    test 안 mock 의무 catalog 시 unittest.mock.AsyncMock 활용 (구체 mock 클래스 미보존).
    """

    @abstractmethod
    async def generate_narrative(self, scope: str, payload: dict) -> str:
        """payload(통계+룰 결과) 받아 narrative 문자열 반환.

        규약 (ADR 0003 3G절):
        - 응답 안 모든 숫자 토큰은 payload 안에 존재해야 함 (수치 hallucination 방지)
        - 호출자가 정규식으로 추출·검증, 실패 시 재생성 1회 후 status='failed'
        """
        ...
