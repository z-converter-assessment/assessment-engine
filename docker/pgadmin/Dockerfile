# pgAdmin4 + servers.json 내장 — prod 가 bind mount·release asset 없이 pull-and-run (A0 정합).
# base docker-compose.yml 이 본 GHCR 이미지를 버전 핀으로 pull. dev override 는 로컬 빌드.
# servers.json(assessment 서버, Host=postgres)이 첫 부팅 시 PGADMIN_SERVER_JSON_FILE 로 자동 등록.
# PassFile 없음 — 첫 연결 시 DB 비번 1회 입력(DB 비번을 이미지·볼륨에 안 남김).
FROM dpage/pgadmin4:8.14
COPY docker/pgadmin/servers.json /pgadmin4/servers.json
