# docs/temp/

본 디렉토리는 임시·외부 공유 자료 모음 (협의 input·제안서·자료 공유용 메모 등).

## 정책 (single source = `.claude/CLAUDE.md` "문서 인덱스" 절)

- 디렉토리 위치 자체는 영구. 안의 파일은 임시 — 자유 작성·삭제.
- 양방향 의존 0 의무:
  - 본 repo 영구 문서·코드 가 본 디렉토리 안 파일을 인용 금지 (메모리 룰 `feedback_no_reference_citations.md`).
  - 본 디렉토리 안 파일 자체도 본 repo 영구 문서·코드를 의존 금지 (외부 공유 시 self-contained 필수 — 외부 reader 가 본 repo 다른 위치 참조 못 함).
- `docs/README.md` 인덱스 표 / `.claude/CLAUDE.md` 문서 인덱스 표 에 추가 안 함.

## 새 파일 작성 시 의무

- 본문 첫 줄에 자료 성격 명시 — 임시·외부 공유·삭제 자유.
- 본 repo 영구 문서 참조 (`docs/reference/...` / CLAUDE.md `#X` 절 인용 등) 사용 금지. 필요 시 본 repo 정책·구조를 self-contained 으로 풀어 박음.
- 본 repo 코드 path 인용 (예: `src/.../foo.py:123`) 도 외부 공유 시 의미 없으므로 추상화 권고 (코드 path 보다 "어느 모듈·어느 함수의 책임" 표현).

## 협의 완료 후 처리

- 결정·합의 채택 시 본 repo 영구 위치 (ADR 등) 로 정공 격상. 본 디렉토리 안 파일은 삭제.
- 협의 결렬·자료 폐기 시 그냥 삭제.
