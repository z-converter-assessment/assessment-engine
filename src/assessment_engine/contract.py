"""계약 버전 상수. 형식과 게이트 규칙은 docs/reference/contracts/ 가 기준.

패키지 루트에 있는 이유 — consumer(수신 게이트)와 web(응답 envelope)이 같은 상수를 읽는다.
어느 한쪽에 두면 다른 쪽이 그 계층을 import 하게 된다.

상대별로 나눈다 — 에이전트는 고객사 내부망에 분산돼 동시 전환이 불가능하고, 외부 시스템은 평가 API
응답만 본다. 한 값을 공유하면 한쪽 사정으로 오른 번호가 다른 쪽에 거짓 신호가 된다.
"""

# wire 메시지(agent -> engine) + task.install(engine -> agent). 결과 보고가 명령과 짝이라 함께 움직인다.
AGENT_CONTRACT_VERSION = "1.0"

# 평가 API 응답 + 그 구조를 그대로 쓰는 export.
API_CONTRACT_VERSION = "1.0"
