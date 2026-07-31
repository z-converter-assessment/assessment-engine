# ADR 0058 — env 템플릿 파일명에 점 prefix 복원

상태: Accepted (2026-07-31)

Supersedes: ADR 0038 (release 에셋명 env.example — 점 prefix 제거)

## Context

ADR 0038 은 배포 템플릿 파일명에서 점을 뺐다. GitHub Release 가 점으로 시작하는 파일을 에셋으로 등록할 때 `default.env.example` 로 변환해 다운로드 경로가 문서와 어긋났기 때문이다.

그 제약이 성립하려면 릴리즈가 env 템플릿을 에셋으로 첨부해야 한다. ADR 0048 이 릴리즈 산출물을 서명·attestation 된 OCI 이미지 단일로 축소하면서 compose·env 첨부를 폐기했고, 같은 ADR 이 0038 을 "대상 소멸" 로 기록했다. 현재 `release.yml` 에 에셋 첨부 단계가 없다.

제약은 사라졌는데 이름만 남아, 생태계 다수 관례(`.env.example`)에서 벗어난 상태가 유지됐다.

## Decision

배포·dev 템플릿 파일명을 점 prefix 로 되돌린다.

- `env.example` -> `.env.example`, `env.dev.example` -> `.env.dev.example`.
- `.gitignore`·`.claudeignore` 의 `.env.*` 패턴에 걸리므로 두 파일을 예외로 되돌린다.
- 템플릿을 이름으로 지목하는 곳을 함께 갱신한다 — `bootstrap.sh` 의 `ENV_TEMPLATE_URL`, compose 주석, 배포·로컬 개발 가이드, PR 템플릿 체크리스트.

## Consequences

파일명이 관례와 일치해 처음 보는 사람이 역할을 이름만으로 판단할 수 있다.

`ls` 기본 출력에서 보이지 않는다. 템플릿의 존재는 README 루트 구성표와 배포 가이드가 알린다.

`bootstrap.sh` 가 받는 raw URL 이 바뀐다. 이미 부트스트랩된 VM 은 `.env` 를 이미 갖고 있어 재실행 시 템플릿을 다시 받지 않으므로 영향이 없고, 새 VM 은 main 반영 후의 URL 을 쓴다.

에셋 첨부가 부활하면 leading-dot 변환 문제가 다시 생긴다. 그때는 첨부 단계에서 이름을 지정하거나 본 결정을 다시 검토한다.
