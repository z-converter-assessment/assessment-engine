# 개발 명령 단일 진입점. `make` 또는 `make help` 로 목록을 본다.
#
# 각 명령이 무엇을 왜 하는지는 docs/guides/ 가 갖는다. 여기는 이름과 실행만 둔다.

.DEFAULT_GOAL := help
.PHONY: help setup dev dev-build dev-down logs test test-unit test-integration test-http test-cov lint format typecheck codegen migrate migration screenshot eol

help: ## 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  make %-16s %s\n", $$1, $$2}'

setup: ## 개발 의존성 설치 (python + node)
	uv sync --frozen --group dev
	pnpm install --frozen-lockfile

dev: .env ## dev 스택 기동 — base + override 머지, 핫리로드 (web http://localhost:8000)
	docker compose up -d

# .env 가 없으면 dev 템플릿에서 만든다. COMPOSE_FILE 이 없어야 compose 가 override 를 자동 머지한다.
.env:
	cp .env.dev.example $@

dev-build: .env ## 이미지 재빌드 후 기동 — 의존성·Dockerfile 을 고쳤을 때
	docker compose up --build -d

dev-down: ## 컨테이너 종료 (볼륨 보존)
	docker compose down

logs: ## web 실시간 로그
	docker compose logs -f web

test: ## 전체 테스트
	uv run pytest

test-unit: ## 단위 테스트
	uv run pytest tests/unit/

test-integration: ## 통합 테스트 (TimescaleDB 컨테이너 기동)
	uv run pytest tests/integration/

test-http: ## HTTP 경계 스냅샷 대조 (기록은 SNAPSHOT_UPDATE=1)
	uv run pytest tests/http/

test-cov: ## 커버리지 측정 (게이트 아님 — 어디가 비었는지 보는 용도)
	COVERAGE_CORE=sysmon uv run coverage run -m pytest tests/unit tests/http
	uv run coverage report

lint: ## ruff (format 검사 + lint)
	uv run ruff format --check .
	uv run ruff check .

format: ## ruff format 적용
	uv run ruff format .

typecheck: ## pyright + tsc(정적 JS)
	uv run pyright
	pnpm run typecheck

codegen: ## OpenAPI -> 클라이언트 TS 타입 재생성
	pnpm run codegen

migrate: ## 마이그레이션 적용 (upgrade head)
	docker compose run --rm migrate alembic upgrade head

migration: ## 마이그레이션 초안 생성 — make migration M="설명"
	docker compose run --rm migrate alembic revision --autogenerate -m "$(M)"

screenshot: ## 화면 캡처 — make screenshot OUT=shots SERVER=<public_id>
	node scripts/screenshot.mjs $(OUT) $(if $(SERVER),--server $(SERVER))

eol: ## OS EOL 카탈로그 갱신 (인터넷 필요)
	uv run python scripts/snapshot_os_eol.py src/assessment_engine/web/services/mappers/os_eol_catalog.json
