# Changelog

## [0.1.1](https://github.com/z-converter-assessment/assessment-engine/compare/v0.1.0...v0.1.1) (2026-05-25)


### Features

* assessment-engine 통합 — 진단·식별·배포 정석화 (배포용 임시) ([#37](https://github.com/z-converter-assessment/assessment-engine/issues/37)) ([e8c8694](https://github.com/z-converter-assessment/assessment-engine/commit/e8c86940d47dfb62c29750313515180629fcd445))

## 0.1.0 (2026-05-18)


### Features

* 2026-05-06기준 에이전트 버전 반영 에이전트-엔진 간 스키마 계약 반영, 웹 ui에 추가된 내용(서비스 및 포트) 반영, README 수정 ([af84892](https://github.com/z-converter-assessment/assessment-engine/commit/af848920e68ae4b4973de6a418cd07588b3a4c2e))
* AI 진단 워커(ADR 0004) + DB schema Alembic 표준화(ADR 0005) ([1501154](https://github.com/z-converter-assessment/assessment-engine/commit/1501154312b5e9a206cfad181344518c7fffd863))
* delta 검증 로직, 차트 개선 (미완성) ([c98e485](https://github.com/z-converter-assessment/assessment-engine/commit/c98e4856204fee6ee408a088082609f6f9e9c67c))
* engine (v0.2.0) ([#5](https://github.com/z-converter-assessment/assessment-engine/issues/5)) ([5b1622f](https://github.com/z-converter-assessment/assessment-engine/commit/5b1622f61ae4a4bcab382bad9fd4c75b405fe989))
* Lima 7 VM 기반 dev 파이프라인 + 분류·attention 시연 환경 구축 ([7e4e437](https://github.com/z-converter-assessment/assessment-engine/commit/7e4e437fe90141dbb9c94fc0332bc06cde994cdf))
* ppt 출력용, 성능 보고서 양식 추가, 리팩토링 일부, 컴포넌트별 문서화 ([a606a49](https://github.com/z-converter-assessment/assessment-engine/commit/a606a494723c4c0576b86f741fd0afdf5f6d26f4))
* ppt 출력용, 웹 ui 수정, 버전 명시하지 않기로 변경 ([74bfd4c](https://github.com/z-converter-assessment/assessment-engine/commit/74bfd4c89bec693fcafe69a50e7b47c1323e765b))
* PROD schema 관리 Alembic 도입 (initial schema + hypertable 수동 보강) ([8253c67](https://github.com/z-converter-assessment/assessment-engine/commit/8253c67a767f748e88e6c40630a33a93ef60a8a0))
* prototype implementation ([cd8f3cb](https://github.com/z-converter-assessment/assessment-engine/commit/cd8f3cb1fe9412143c6db876fba29c44d3b5c633))
* Task 별도 큐 모델 + dev TLS 2-port + 운영 가시성 (ADR 0007/0008) ([6aa6e83](https://github.com/z-converter-assessment/assessment-engine/commit/6aa6e83fa9411a50903664a9fa6642499eeb857d))
* 대시보드 통합 시각화 + JSON Export v3 + 정책 정비 ([d0f6065](https://github.com/z-converter-assessment/assessment-engine/commit/d0f60650f96df7c8e5d6b6e4e8c4ad28455afc89))
* 서버 목록 상단 risk_top + attention 두 시선 요약 ([0b147cd](https://github.com/z-converter-assessment/assessment-engine/commit/0b147cdd0ffe763db4646192548a53cb23e41509))
* 시연용, 구조 정리 및 리팩토링, vm 부하 발생을 위한 베어그란트 로직 적용, 단위/모듈 파이테스트 작성 ([b785732](https://github.com/z-converter-assessment/assessment-engine/commit/b7857320b4ff00fc58e95f3143080589a10445d9))
* 웹 UI 개선 및 레포지토리 및 서비스 계층 로직 일부 변, 임시 필터링(추후 스키마 계약 필요) ([0b2c939](https://github.com/z-converter-assessment/assessment-engine/commit/0b2c9391fe0eca4fbcccb2c30b2e599086ce0842))
* 진단 워커·보고서·multi-node·외부 인프라 quickstart 묶음 ([#10](https://github.com/z-converter-assessment/assessment-engine/issues/10)) ([3ef0d3e](https://github.com/z-converter-assessment/assessment-engine/commit/3ef0d3e774806cddc1ee1302ca1a0d8749f77d3a))
* 클로드 하네스 정비 ([b9bb4d8](https://github.com/z-converter-assessment/assessment-engine/commit/b9bb4d883f4a01e4cac359251c18bda7076f7df9))
* 평가 산출물(서버 발견·태스크 발행·정제 export·USE 보고서), 컨슈머 부가 시그널 ([7c2f299](https://github.com/z-converter-assessment/assessment-engine/commit/7c2f299a43bcb135e4fb59a1887972e95d28515a))


### Bug Fixes

* correct detail.html metric references and convert timestamps to KST ([60c06c5](https://github.com/z-converter-assessment/assessment-engine/commit/60c06c50197d83fadaf5c9f24d81ddb5059d32c0))
* dev-up.sh 정합성 + 진단 unit test ruff import sort ([69f60cb](https://github.com/z-converter-assessment/assessment-engine/commit/69f60cb0f9342cf140828b31cd6b82271fcf7e3d))
* pgAdmin 이메일 RFC 준수 + Alembic transitional 안내 ([bcc8dfe](https://github.com/z-converter-assessment/assessment-engine/commit/bcc8dfe9e43e162c48f83cb1f39c1197f37ada05))
* README 정의·산출물 설명 추상화 ([d3e77f5](https://github.com/z-converter-assessment/assessment-engine/commit/d3e77f5b286814e92b157a448e4975de829b91b8))
* README.md에 프로젝트 개요 작성, docs/ 디렉토리에 주제별 문서 작성 ([#6](https://github.com/z-converter-assessment/assessment-engine/issues/6)) ([2de02ff](https://github.com/z-converter-assessment/assessment-engine/commit/2de02ff9c6f7e1ba146dac7c715f12ac820f285a))

## Changelog

본 파일은 release-please가 자동 갱신 — Conventional Commits 기반 (ADR 0013).
직접 편집 금지 (다음 release-please 실행 시 덮어쓰기).
