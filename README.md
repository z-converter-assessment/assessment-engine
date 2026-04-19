# assessment-worker

서버 Assessment 서비스의 메시지 큐 및 Worker입니다.

---

## 기술 스택

---

## 프로젝트 구조

---

## 브랜치 전략

```
main          # 배포용. 직접 push 금지
develop       # 개발 통합. PR로만 머지
feature/xxx   # 기능 개발
fix/xxx       # 버그 수정
chore/xxx     # 설정 변경
```

**작업 흐름**

```
develop에서 브랜치 생성
→ 작업 완료 후 develop으로 PR
→ 1명 이상 리뷰 승인 후 머지
```

---

## 커밋 컨벤션

| 타입 | 설명 | 예시 |
|---|---|---|
| `feat` | 새로운 기능 추가 | `feat: 메시지 큐 consumer 구현` |
| `fix` | 버그 수정 | `fix: DB 커넥션 풀 타임아웃 오류 수정` |
| `chore` | 설정, 패키지 변경 | `chore: requirements.txt 업데이트` |
| `docs` | 문서 수정 | `docs: README 로컬 실행 방법 추가` |
| `refactor` | 리팩토링 | `refactor: 메트릭 파싱 로직 함수 분리` |
| `test` | 테스트 코드 | `test: Worker 유닛 테스트 추가` |
| `style` | 포맷 등 스타일 변경 | `style: 들여쓰기 정리` |
