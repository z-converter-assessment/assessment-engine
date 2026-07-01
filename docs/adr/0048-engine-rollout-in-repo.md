# ADR 0048 — 엔진 배포(rollout)를 본 repo 로 통합 (compose 매체 + self-hosted runner)

상태: Accepted (2026-07-01)

Supersedes: ADR 0012 (wheel CI 산출물), ADR 0017 (wheel+image 이원 산출물)
Refines: ADR 0006 (Withdrawn — IaC out-of-scope), ADR 0030 (notify-infra CD dispatch), ADR 0035/0036 (compose base/override·배포), ADR 0038 (release 에셋명)

## Context

기존 배포 모델(#A0·ADR 0006·0030)은 본 repo 를 "artifact 게시 + contract 정의"로 한정하고, 실제 rollout 은
범위 밖(별도 인프라 repo `assessment-infra`)에 뒀다. release.yml 은 wheel + image 두 채널을 발행하고
`notify-infra` job 이 `assessment-infra` 로 `repository-dispatch` 신호만 던졌다 — 그 신호를 수신해 배포를
수행하는 곳은 존재하지 않았다(CD 미구현).

배포 매체를 docker compose 단일로 고정하고, 엔진을 "정확히 배포"하는 rollout(pull -> migration -> up ->
health -> rollback)까지 본 repo 가 소유하도록 성격을 재정의한다. 별도 인프라 repo 없이 단일 VM 에 엔진을
배포한다.

경계 결정(선행 정리 ADR 0047 로 pgAdmin 제거 완료):
- 배포 대상은 내부망/OpenStack VM 1 대(prod 단일 환경). GitHub 클라우드에서 inbound 접근 불가, outbound 만 가능.
- 가치·반복성이 몰린 곳은 rollout 이다(migration 선행·health gate·rollback). 반면 VM 생성·OS 설정은 거의
  안 바뀌고 넣으면 복잡도가 재유입된다(ADR 0006 이 밀어낸 것).

## Decision

엔진 rollout 을 본 repo 워크플로로 구현한다. compose 단일 매체, self-hosted runner 채널.

- 배포 매체: docker compose (prod = base `docker-compose.yml` + `docker-compose.secrets.yml`). 배포 대상은
  release.yml 이 발행한 서명·attestation 된 GHCR 엔진 이미지. compose base 는 배포 대상(rollout)으로 실사용.
- 채널: `deploy.yml` `runs-on: [self-hosted, assessment-prod]` — 잡이 VM 로컬 실행. VM 은 outbound HTTPS
  폴링만(inbound·SSH 불요). compose 는 localhost 작업.
- 트리거: `workflow_dispatch`(version 입력) + `production` Environment protection(운영자 승인 1회). release
  성공 후 자동 배포 안 함 — prod 단일 VM 비가역 작업 앞 사람 게이트.
- rollout 시퀀스: cosign verify(공급망 게이트) -> compose pull -> `up -d`(base 의 migrate init-container 가
  web/consumer 기동 전 alembic upgrade head 실행 = migration 선행 내재) -> `/health` gate -> 실패 시 rollback.
- rollback: 배포 디렉토리 `.env` 의 `ENGINE_IMAGE` 핀이 현재 버전. 교체 전 현재 핀을 `.last-good` 으로 capture
  -> health 실패 시 그 이미지로 되돌려 재기동(capture-before-swap). 상태는 VM 로컬 파일.
- 부트스트랩 경계: docker engine + compose plugin 설치·배포 디렉토리·secret 스캐폴딩·self-hosted runner 등록은
  1 회성 멱등 `bootstrap.sh`. 파이프라인 본체엔 넣지 않는다. VM 생성·서브넷·OS 설정 = 여전히 범위 밖
  (provisioning 도구 미도입).
- 릴리즈 표면 축소: wheel·sdist·SHA256SUMS·CycloneDX SBOM 파일·Sigstore 서명·compose/env release 첨부·
  `notify-infra`(assessment-infra dispatch)를 폐기. release.yml = resolve-version + release-image 2 job.
  릴리즈 산출물 = 서명(cosign)·SBOM(SPDX attestation)·provenance 된 OCI 이미지 단일(GitHub Release·wheel 없음).

## Consequences

- 별도 인프라 repo·CD dispatch 체인 없이 단일 VM 에 엔진 배포가 성립한다. 배포 = `deploy.yml` 수동 실행 +
  승인. 재배포 = version 입력만.
- 릴리즈 표면이 정석(서명·attestation OCI 이미지 단일)으로 수렴. 별도 파일 애셋(체크섬·SBOM 파일·서명 파일)이
  이미지 attestation 으로 귀속돼 이중(서명 2채널·SBOM 2포맷)이 해소.
- wheel + venv + systemd 배포 경로 폐기 — 운영자 선택권(ADR 0017 A/B/C)에서 compose 단일로 축소. air-gapped
  는 `docker save/load` 로 대응.
- 배포 매체가 compose 하나라 dev-prod parity 가 더 단단해진다(dev = base+override, prod = base+secrets,
  배포 = base+secrets rollout).
- 한계: 단일 VM·단일 환경. staging 승격·다수 호스트·zero-downtime(blue-green)은 미도입 — 필요 시 별도 ADR.
  self-hosted runner 는 워크플로를 VM 에서 실행하므로 fork PR·비신뢰 브랜치 실행 차단이 운영 의무(repo 설정).

## 관계

- #A0(CLAUDE.md) "배포 인프라 범위 밖" 을 정정 — 엔진 rollout(compose 매체)은 범위 안. VM provisioning(IaC)은
  여전히 범위 밖. `docs/operations/deployment.md`·`release.md` 를 compose+rollout 모델로 재작성.
- ADR 0006(Withdrawn) 의 "IaC out-of-scope" 를 refine — 배포를 전부 밀어내던 경계를 "rollout 포함 / provisioning
  제외"로 재획정. 기존 ADR 본문 불변(스크럽 금지).
- ADR 0012·0017 supersede — wheel 산출물·wheel+image 이원 폐기, image 단일.
- ADR 0030 의 `notify-infra`(assessment-infra dispatch) 정정을 supersede — dispatch 폐기, in-repo deploy.
  resolve-version single-source·이미지 등가성 검증은 존속.
- ADR 0035/0036 refine — compose base 는 릴리즈 첨부 애셋이 아니라 배포 대상(rollout)으로 실사용. release
  asset 에서 compose/env 첨부 폐기.
- ADR 0038(release 에셋명 env.example) 은 compose/env release 첨부 폐기로 대상 소멸 — 역사 기록으로 존속.
- ADR 0047 pgAdmin 제거의 후속 — 배포 토폴로지 축소 위에서 rollout 구현.
- #F9 동시 갱신: `deploy.yml`(신규)·`bootstrap.sh`(신규)·`release.yml`·`docs/operations/{deployment,release,env}.md`·
  `README.md`·CLAUDE.md #A0·ADR 인덱스.
