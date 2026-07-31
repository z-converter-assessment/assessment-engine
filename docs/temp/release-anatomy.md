# 릴리즈 해부 — 한 번의 릴리즈가 만드는 것

임시 자료 — 내부 학습용, 삭제 자유. 릴리즈 파이프라인과 OCI 아티팩트 구조를 처음부터 따라가려고 쓴 문서다.

버전 하나를 올려 머지한 순간부터 배포 대상 VM 이 그 이미지를 받아 기동할 때까지 무엇이 어디서 만들어지고 어디에 남는지를 다룬다.

기준은 develop 계열 브랜치의 워크플로 코드이며, main 승격 후 첫 릴리즈부터 적용된다. 레지스트리에 쌓여 있는 5 건은 main 브랜치의 이전 방식으로 발행됐고 트리거와 태그 생성 주체가 다르다. 두 방식의 차이는 10 절에, 레지스트리 잔존 상태는 11 절에 정리했다.

구조 설명에 쓰인 blob 목록·digest·attestation·인증서는 `1.2.1` 릴리즈에서 뽑은 실측값이다. 빌드 설정(플랫폼·provenance·SBOM)은 두 방식이 같으므로 이 부분은 그대로 유효하다.

## 1. 등장하는 장소 5곳

무엇이 어디서 일어나는지가 잡히지 않으면 나머지가 흐려진다. 장소부터 고정한다.

| 장소 | 정체 | 하는 일 |
|------|------|---------|
| 개발 머신 | 로컬 | 코드 작성, git push. 빌드하지 않음 |
| github.com | 저장소 | 코드 보관, main push 감지, 워크플로 실행 지시 |
| Actions runner | GitHub 이 띄우는 임시 Ubuntu VM | 빌드·push·서명 수행 후 파기 |
| ghcr.io | 컨테이너 레지스트리 | 이미지와 서명 저장 |
| sigstore | 공개 서비스 (Fulcio, Rekor) | 인증서 발급, 서명 기록 |

빌드는 개발 머신에서 일어나지 않는다. GitHub 이 자기 인프라에 VM 을 하나 새로 띄우고 거기서 워크플로 스텝을 순서대로 실행한 뒤 그 VM 을 없앤다.

## 2. 릴리즈 1회의 시간순 전 과정

develop 계열 방식의 단계 순서다. 빌드 소요는 멀티아치 기준 약 4 분이고 runner 는 Ubuntu 24.04.4 LTS 다. main 브랜치 방식은 시작점과 마지막 스텝만 다르며 10 절에 정리했다.

```
[dev machine]
  bump version in pyproject.toml, merge to main
        |
        v
[github.com]  detects push to main -> triggers release workflow
        |
        v
[runner VM]   GitHub boots a fresh Ubuntu VM
              docker build --platform linux/amd64,linux/arm64      (~4 min)
                  -> layer blobs + config blob
                  -> image manifest per platform
                  -> attestation manifest per platform
                  -> index tying them together
              docker push  --------------------------> [ghcr.io]  stored
              cosign sign
                  generate key pair (in VM memory)
                  get OIDC token from GitHub
                  send token + public key ------------> [fulcio]
                                     <----------------- certificate, valid 10 min
                  sign the index digest with the private key
                  record the event -------------------> [rekor]
                  push signature ---------------------> [ghcr.io]  stored
                  discard the private key
              git tag v1.2.1 ------------------------> [github.com]
              VM destroyed
```

빌드도 서명도 전부 runner VM 안에서 일어나고, 그 VM 은 작업이 끝나면 파기된다. 개발 머신에는 아무것도 남지 않는다.

## 3. 레지스트리가 저장하는 3가지

이미지는 파일 하나가 아니다. 저장 단위가 셋으로 나뉜다.

| 단위 | 정체 | 패키지 버전으로 세는가 |
|------|------|----------------------|
| blob | 실제 바이트 덩어리. 레이어 tar, config JSON | 아니오 |
| manifest | blob 목록과 메타데이터를 담은 JSON. 이미지 하나의 정의서 | 예 |
| index | manifest 여러 개를 묶은 JSON. manifest list 라고도 함 | 예 |

`docker build` 는 Dockerfile 을 위에서 아래로 실행한다. `RUN` 과 `COPY` 각각이 파일시스템 변경분을 만들고, 그 결과가 레이어다. 레이어와 실행 설정(config)은 blob 으로 저장되고, 그것들의 목록을 적은 JSON 이 manifest 다.

`1.2.1` 의 amd64 이미지는 blob 9 개로 구성된다.

```
image manifest (linux/amd64)
  config blob   sha256:6e073b19...        8,565 bytes
  layer blob 1  sha256:062e4506...   29,780,905
  layer blob 2  sha256:98db2485...    1,293,301
  layer blob 3  sha256:48347b15...   12,108,405
  layer blob 4  sha256:fd079632...          250
  layer blob 5  sha256:721635c9...           93
  layer blob 6  sha256:fdbc9fec...      784,061
  layer blob 7  sha256:1167c46c...   28,752,062
  layer blob 8  sha256:8a4b48eb...        1,120
```

저장 구조는 평면적이다. blob 과 manifest 가 나란히 저장되고 서로를 digest 로 참조할 뿐, 물리적으로 품고 있지 않다. blob 은 릴리즈끼리 공유되므로 두 번째 릴리즈부터는 바뀐 레이어만 새로 올라간다. `docker pull` 이 이미 가진 레이어에 "Already exists" 를 출력하는 것이 이 때문이다. 레지스트리 패키지 페이지가 세는 버전 수에 blob 이 들어가지 않는 이유도 같다. blob 은 이미지 하나에 귀속되지 않는다.

## 4. digest 와 태그

digest 는 별도로 생성하는 값이 아니라 그 객체의 바이트를 sha256 해싱한 결과다. 내용 자체가 이름이 된다.

`1.2.1` 의 index 원문 1,609 bytes 를 직접 해싱한 값과 레지스트리가 알려준 digest 가 일치한다.

```
sha256:6eb2366b982e6059ad63ec185b55330348fb029d81282180dbe0405e691ec1f9
```

이 관계가 층층이 물린다.

```
layer file        -sha256-> layer digest
                               |  (written inside the image manifest JSON)
image manifest    -sha256-> manifest digest
                               |  (written inside the index JSON)
index JSON        -sha256-> index digest    <- tag "1.2.1" points here
```

아래에서 1 바이트만 바뀌어도 그 digest 가 바뀌고, 그 값을 품은 상위 JSON 이 바뀌어 연쇄로 index digest 까지 바뀐다. 그래서 index digest 하나가 그 아래 전부를 대표한다.

태그는 저장 객체가 아니라 digest 를 가리키는 이름표다. 여러 개를 같은 digest 에 붙일 수 있고 나중에 다른 digest 로 옮길 수도 있다. `1.2.1`, `1.2`, `1`, `latest` 네 개가 같은 index 하나를 가리키며, 다음 릴리즈가 나오면 `latest` 와 `1` 과 `1.2` 는 새 digest 로 옮겨가고 `1.2.1` 만 남는다. 배포에서 정확한 버전을 pin 하고 서명 대상을 태그가 아닌 digest 로 잡는 이유가 여기 있다. 태그는 움직이고 digest 는 움직이지 않는다.

## 5. 릴리즈 1회가 남기는 매니페스트

```
tags: 1.2.1, 1.2, 1, latest
  |
  v
[1] index                                    <- tags point here
      +-- [2] image manifest  linux/amd64      <- 9 blobs
      +-- [3] image manifest  linux/arm64      <- 9 blobs
      +-- [4] attestation     for amd64        <- SBOM + provenance
      +-- [5] attestation     for arm64        <- SBOM + provenance

tag: sha256-<index digest>
  |
  v
signature manifests                          <- cosign, one bundle per sign call
```

빌드가 남기는 것은 위 5 개로 고정이다. 서명 쪽 개수는 빌드가 아니라 cosign 호출 횟수를 따라간다. develop 계열 코드는 index digest 에 한 번만 호출한다.

각 항목은 워크플로 설정과 1 대 1 로 대응한다.

| 산출물 | 원인 | 기본 동작인가 |
|--------|------|--------------|
| image manifest 2 개 | 빌드 대상 플랫폼을 linux/amd64 와 linux/arm64 로 지정 | 아니오. 기본은 빌더 호스트 아키텍처 1 개 |
| attestation 2 개 | provenance 와 SBOM 생성 옵션 | provenance 는 push 시 자동 부착, SBOM 은 명시해야 생성 |
| index 1 개 | 매니페스트가 둘 이상이면 자동 생성 | 자동 |
| signature | 빌드와 무관한 별도 스텝의 cosign 호출 | 도커 기능이 아님 |

멀티아치가 필요한 이유는 실행 파일이 CPU 아키텍처마다 다르기 때문이다. amd64 서버와 arm64 서버가 같은 이름으로 pull 하면 레지스트리는 index 를 돌려주고, docker 가 그 안에서 자기 플랫폼 항목을 골라 해당 manifest 만 내려받는다.

attestation 은 실행되는 이미지가 아니라 빌드에 대한 증명 문서다. 플랫폼마다 하나씩 생기고, 그 안에 두 문서가 레이어로 들어간다.

```
application/vnd.in-toto+json | https://spdx.dev/Document      | 2,258,147 bytes   <- SBOM
application/vnd.in-toto+json | https://slsa.dev/provenance/v1 |    27,602 bytes   <- provenance
```

SBOM 은 이미지에 들어간 패키지 목록이고, provenance 는 어느 저장소의 어느 커밋에서 어떤 워크플로로 빌드됐는지를 기록한 SLSA 문서다. 실행 가능한 이미지가 아니라서 이미지 조회 도구에는 플랫폼이 `unknown/unknown` 으로 표시된다.

## 6. 서명과 인증서

가장 헷갈리는 지점이다. 인증서로 서명하는 것이 아니다. 둘은 다른 물건이다.

- 서명은 개인키로 만든다. 대상 데이터를 개인키로 처리해 만든 값이며, 짝이 되는 공개키로만 검증된다.
- 인증서는 그 공개키의 주인이 누구인지 제3자가 보증한 별도 문서다.

인증서가 필요한 이유는 공개키만으로는 주인을 알 수 없기 때문이다. 검증자는 서명이 그 공개키와 맞는지까지만 확인할 수 있고, 그 키가 우리 릴리즈 파이프라인 것인지 공격자 것인지는 판단하지 못한다. 그 빈칸을 채우는 것이 인증서이므로 서명과 인증서를 함께 저장한다.

서명 스텝이 도는 동안의 순서다.

```
[1] cosign generates a fresh key pair            (private + public, in VM memory)
[2] GitHub Actions issues an OIDC token          ("this job is the release workflow of this repo")
[3] cosign sends the OIDC token + public key  -> fulcio
[4] fulcio verifies the token and returns a certificate
        "the owner of this public key is that workflow", valid 10 minutes
[5] cosign signs the index digest with the PRIVATE key
[6] the private key is discarded; signature + certificate go to the registry
```

서명하는 주체는 개인키이며, 인증서는 4 단계에서 받아 6 단계에 첨부되는 문서다. 인증서가 서명 행위를 하지 않는다.

`1.2.1` 서명 번들에서 꺼낸 인증서 실물이다.

```
issuer      = O=sigstore.dev, CN=sigstore-intermediate
notBefore   = Jul 21 06:32:05 2026 GMT
notAfter    = Jul 21 06:42:05 2026 GMT
subject     = (empty)

X509v3 Subject Alternative Name: critical
  URI:https://github.com/z-converter-assessment/assessment-engine
      /.github/workflows/release.yml@refs/tags/v1.2.1

OIDC issuer = https://token.actions.githubusercontent.com
commit      = 3d5e7bcaae467d3a71e27365a852c988890eb553
runner      = github-hosted
```

일반적인 TLS 인증서와 달리 subject 에 사람이나 회사 이름이 없다. 대신 워크플로 파일 경로가 URI 로 박혀 있고 빌드 커밋 해시까지 들어 있다. 이 인증서가 증명하는 것은 특정 개인이 아니라 특정 저장소의 특정 워크플로가 특정 커밋에서 실행됐다는 사실이다.

개인키가 서명 직후 사라지므로 유출될 장기 키가 존재하지 않는다. 키 보관·교체·폐기 절차가 통째로 없어지는 대신, 신뢰의 근거가 키 관리에서 GitHub OIDC 와 공개 로그의 증언으로 옮겨간다. 이 방식을 keyless 라 부른다.

인증서 유효기간이 10 분인데도 몇 달 뒤 검증이 성립하는 이유는 Rekor 때문이다. 서명 번들에는 Rekor 에 기록된 시각의 영수증이 함께 담기고, 검증자는 그 시각이 인증서 유효구간 안인지만 확인한다. 만료 여부와 무관하게 판정된다.

## 7. digest 가 있는데 서명이 필요한 이유

digest 는 무결성만 답하고 출처는 답하지 못한다. 두 질문이 다르다.

운영자가 `1.2.1` 을 pull 하면 docker 가 받은 바이트를 해싱해 digest 와 대조하므로 전송 중 손상이나 중간자 변조는 걸린다. 그런데 공격자가 레지스트리 push 권한을 가진 토큰을 탈취해 자기 이미지를 올리고 `1.2.1` 태그를 그쪽으로 옮기면, 그 이미지도 자기 내용에 맞는 digest 를 가지므로 해시 대조는 그대로 통과한다.

- digest 가 답하는 것 — 받은 바이트가 이 이름표와 일치하는가
- digest 가 답하지 못하는 것 — 이 digest 가 우리 파이프라인에서 나온 것인가

서명 검증은 네 단계를 거친다.

1. 태그로 index digest 를 얻고, `sha256-<digest>` 형태의 태그에서 서명과 인증서를 가져온다.
2. 인증서가 sigstore 루트에서 발급된 진짜인지 확인한다.
3. 인증서의 SAN URI 가 지정한 신원 패턴과 맞는지 본다.
4. 인증서 안의 공개키로 서명을 검증하고, 서명 대상이 그 index digest 인지 확인한다.

공격자는 3 번을 통과할 수 없다. 우리 저장소 이름이 박힌 인증서를 받으려면 우리 저장소의 GitHub Actions 에서 실행된 OIDC 토큰이 필요하기 때문이다.

서명 대상이 index digest 인 점이 중요하다. 해시 연쇄 덕분에 index digest 하나에 서명하면 그 아래 플랫폼별 이미지와 attestation 이 전부 함께 보증된다.

## 8. 배포에 필요한 재료 3가지

여기까지가 이미지 이야기였는데, 배포는 이미지만으로 되지 않는다. 재료가 셋이고 출처가 각각 다르다.

| 재료 | 출처 | 릴리즈가 만드는가 |
|------|------|------------------|
| 엔진 이미지 | GHCR. 버전 태그로 pin | 예 |
| compose 파일 2 개 | 저장소의 태그 ref. raw 경로에서 curl | 아니오 |
| `.env` 와 secret 파일 | 배포 VM 로컬. 부트스트랩이 배치하고 운영자가 값을 채움 | 아니오 |

받아오는 compose 는 base 와 file-secret overlay 둘이다. dev 용 override 는 받지 않는다.

### compose 는 언제 그 위치에 등록되나

등록하는 절차가 따로 없다. compose 는 저장소 루트에 커밋된 평범한 파일이고, 릴리즈 파이프라인에 이 파일을 어딘가로 올리는 스텝은 존재하지 않는다.

raw 경로의 형식이 `raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>` 인데, `<ref>` 자리에는 브랜치 이름과 태그 이름과 커밋 해시가 모두 들어갈 수 있다. 태그 이름을 넣으면 그 태그가 가리키는 커밋 시점의 파일 내용이 응답된다. 따라서 그 주소가 유효해지는 시점은 파일을 올리는 시점이 아니라 태그가 생기는 시점이다.

- develop 계열 방식 — 이미지 발행과 서명이 끝난 뒤 워크플로가 태그를 만드는 순간
- main 브랜치 방식 — 운영자가 로컬에서 태그를 push 하는 순간

태그가 사라지면 주소도 함께 사라진다. 실제 응답을 보면 관계가 분명하다.

```
v1.2.1/docker-compose.yml   -> HTTP 200
v1.2.0/docker-compose.yml   -> HTTP 200
v0.11.2/docker-compose.yml  -> HTTP 404      (tag deleted)
main/docker-compose.yml     -> HTTP 200      (branch name also works as a ref)
```

`v1.2.1` 은 커밋 `3d5e7bca` 를 가리키고 그 커밋의 트리에 compose 파일들이 들어 있다. 이미지 버전과 compose 토폴로지 버전이 같은 ref 로 묶이는 것이 이 구조다. `v1.2.1` 이미지를 배포하면 반드시 그 커밋 시점의 compose 로 기동한다.

### 두 재료의 신뢰 경로가 다르다

이미지는 pull 전에 서명을 검증하지만 compose 에는 서명이 없다. compose 의 무결성은 TLS 전송과 태그가 다른 커밋으로 옮겨가지 않는다는 전제에 기댄다. 그 전제를 저장소 규칙이 받쳐주고 있으며, `v*` 태그에 삭제 금지와 non-fast-forward 금지가 걸려 있어 한 번 붙은 태그가 다른 커밋을 가리키도록 바꿀 수 없다.

## 9. 배포는 이 흐름을 거꾸로 탄다

배포 대상 VM 에서 배포 스크립트에 버전을 넘겨 실행하면 다음 순서로 진행된다.

```
[deployment VM]
  cosign verify <version>  --> reads the signature from [ghcr.io]
                           --> checks certificate identity and the rekor record via [sigstore]
       fail -> abort, nothing is pulled
       pass |
            v
  fetch compose files from raw.githubusercontent.com/<repo>/<tag>/
       (the git tag pins image version and compose topology to the same ref)
            v
  docker compose pull  --> [ghcr.io] index -> picks amd64 or arm64 -> downloads blobs
            v
  migration init-container -> web / consumer / worker start -> health check
       fail -> roll back to the last known good image and restart
```

내부망 VM 이 바깥으로 나가 이미지와 compose 를 받아오는 방향이며, 바깥에서 VM 으로 밀어 넣지 않는다.

## 10. git 태그가 하는 일

태그는 장식이 아니라 파이프라인이 읽고 쓰는 상태다. 다만 방식에 따라 태그의 위치가 입력에서 출력으로 바뀐다.

| | main 브랜치 방식 | develop 계열 방식 |
|---|---|---|
| 릴리즈 트리거 | 운영자가 로컬에서 `v*` 태그를 push | main 브랜치 push |
| 버전 단일 진실 | git 태그. 빌드 시 태그에서 버전을 derive | `pyproject.toml` 의 version |
| 태그 생성 주체 | 운영자 (로컬 자격증명) | runner VM (발행·서명 성공 후 마지막 스텝) |
| 태그의 성격 | 파이프라인의 입력 | 파이프라인의 출력 |

레지스트리에 있는 5 건은 왼쪽 방식으로 발행됐다. 운영자가 태그를 push 하는 것이 시작점이었고 워크플로는 태그를 만들지 않았다. 오른쪽 방식에서는 버전 파일을 올린 커밋이 시작점이고, 워크플로가 같은 버전의 태그가 이미 있으면 중복으로 보아 건너뛰며, 발행과 서명이 모두 성공한 뒤에야 태그를 남긴다. 앞 단계가 실패하면 태그가 없으므로 그대로 재시도할 수 있다.

두 방식 모두에서 공통인 역할이 하나 있다. 배포 스크립트가 그 태그의 raw 경로에서 compose 파일을 받으므로, 태그는 이미지 버전과 compose 토폴로지 버전을 같은 ref 로 묶는 축이다.

## 11. 레지스트리에 남아 있는 값

앞 절들이 develop 계열 코드가 만들어낼 결과를 설명한 반면, 이 절은 레지스트리에 실제로 쌓여 있는 상태다. 5 건 모두 main 브랜치 방식(운영자 태그 push 트리거)으로 발행됐다.

```
릴리즈           5 건 (1.0.0, 1.1.0, 1.1.1, 1.2.0, 1.2.1)
GHCR 패키지 버전  55 개 = 릴리즈당 11 개
git 태그          5 개
태그와 이미지     1 대 1 대응, 어느 쪽에도 누락 없음
서명 검증         5 건 전부 통과
```

릴리즈당 11 개의 내역은 index 1, 플랫폼 이미지 2, attestation 2, 서명 index 1, 서명 번들 5 다. 앞 5 개는 develop 계열 코드로 빌드해도 같고 서명 쪽 6 개가 다르다. 서명 번들이 여러 개인 것은 호출이 태그 개수만큼 반복된 결과이며, develop 계열 코드는 digest 에 한 번만 호출하므로 다음 릴리즈의 서명 매니페스트는 이보다 적다. 정확한 개수는 다음 릴리즈가 나온 뒤 확인한다.

서명 조회 경로는 `sha256-<digest>` 태그다. OCI 표준에는 referrers API 라는 정식 경로도 있으나 GHCR 이 빈 응답을 주기 때문에 태그 방식이 쓰인다.

## 12. 용어

| 용어 | 뜻 |
|------|-----|
| blob | 레지스트리에 저장된 바이트 덩어리. 레이어와 config |
| manifest | blob 목록을 담은 JSON. 이미지 하나의 정의 |
| index | manifest 여러 개를 묶은 JSON. 태그가 붙는 지점 |
| digest | 객체의 바이트를 sha256 해싱한 값. 내용이 곧 이름 |
| 태그 | digest 를 가리키는 이름표. 옮겨 다닐 수 있음 |
| attestation | 빌드에 대한 증명 문서. SBOM 과 provenance |
| SBOM | 이미지에 포함된 패키지 목록 (SPDX 형식) |
| provenance | 빌드 출처 기록. 저장소·커밋·워크플로 (SLSA 형식) |
| cosign | 컨테이너 이미지 서명 도구 |
| keyless | 장기 개인키 없이 단기 인증서로 서명하는 방식 |
| Fulcio | OIDC 신원을 확인해 단기 인증서를 발급하는 sigstore 인증기관 |
| Rekor | 서명 사건을 기록하는 sigstore 공개 투명성 로그 |
| OIDC | 신원 제공자가 발급하는 신원 토큰 규격 |
| runner | 워크플로를 실행하는 임시 VM |
