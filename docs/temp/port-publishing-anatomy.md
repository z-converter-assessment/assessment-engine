# 포트 퍼블리싱 해부 — compose 한 줄이 커널에 만드는 것

임시 자료 — 내부 학습용, 삭제 자유. compose 의 `ports:` 한 줄이 리눅스 네트워크 스택 어디에 무엇을 만드는지 처음부터 따라가려고 쓴 문서다.

출발점은 배포 compose 의 이 줄이다.

```yaml
ports:
  - "127.0.0.1:5432:5432"
```

호스트 포트와 컨테이너 포트를 잇겠다는 의도까지는 명백한데, 앞에 붙은 `127.0.0.1` 이 무엇을 좁히는지, 그 좁힘이 어느 계층에서 강제되는지가 문법만 봐서는 드러나지 않는다. netfilter 층위부터 세우고 패킷 하나를 끝까지 따라간다.

## 1. 등장인물 8 개

포트 퍼블리싱을 설명하는 용어들이 서로 다른 계층에 산다. 한 줄에 나열되면 엉키므로 계층부터 고정한다.

| 계층 | 이름 | 정체 |
|------|------|------|
| 커널 | netfilter | 패킷 처리 프레임워크. 아래 4 개를 담는 그릇 |
| 커널 | hook | 패킷이 커널을 지나며 거치는 5 개 지점 |
| 커널 | table | 목적별 규칙 분류. nat · filter · mangle · raw |
| 커널 | chain | (hook, table) 칸에 놓인 규칙 묶음. 빌트인 + 사용자 정의 |
| 커널 | rule / target | 매칭 조건 한 줄과 그 결과 동작. DNAT · ACCEPT · DROP · jump |
| 유저스페이스 | iptables | 위 규칙을 써넣는 CLI. 패킷 처리에 관여하지 않는 도구 |
| 유저스페이스 | dockerd | 규칙을 써넣고 프록시 프로세스를 띄우는 데몬 |
| 유저스페이스 | docker-proxy | 소켓 하나를 들고 있는 평범한 TCP 프록시 프로세스 |

같은 계층인 항목은 하나도 없다. PREROUTING 은 hook, nat 은 table, DOCKER 는 chain, DNAT 는 target, iptables 는 설정 도구, docker-proxy 는 netfilter 와 무관한 별개 프로세스다.

패킷을 실제로 옮기는 주체는 커널이고, 유저스페이스 3 개 중 둘(iptables · dockerd)은 규칙을 써넣기만 한다. docker-proxy 만이 패킷을 직접 만지는 유저스페이스 참여자다.

## 2. hook — 패킷이 지나는 길 5 곳

```
        NIC
         |
    [ PREROUTING ] -- dst is another host --> [ FORWARD ] --+
         |                                                  |
    dst is me                                               |
         |                                                  v
    [ INPUT ]                                       [ POSTROUTING ]
         |                                                  ^
         v                                                  |
    local socket -- process -- [ OUTPUT ] -----------------+
```

들어온 패킷은 PREROUTING 을 먼저 지난다. 거기서 커널이 목적지 IP 를 보고 갈림길을 정한다 — 호스트 자신이면 INPUT, 남의 것이면 FORWARD 다. 호스트의 프로세스가 만들어 내보내는 패킷은 OUTPUT 에서 출발하고, 나가는 패킷은 전부 POSTROUTING 을 마지막으로 지난다.

주소 변환을 PREROUTING 에서 하는 이유가 이 그림에 있다. 목적지를 컨테이너 IP 로 바꾸는 일이 갈림길 판정보다 먼저 끝나야 패킷이 INPUT 이 아니라 FORWARD 로 빠진다. 순서가 반대면 컨테이너로 갈 패킷이 호스트 자신에게 배달된다.

## 3. table — 한 hook 에 여러 겹

hook 하나에 table 이 여러 개 걸리고 정해진 우선순위로 차례로 실행된다. 격자로 보면 어디에 무엇이 있는지가 드러난다.

```
             PREROUTING   INPUT   FORWARD   OUTPUT   POSTROUTING
   raw           o                            o
   mangle        o          o        o        o           o
   nat           o          o                 o           o
   filter                   o        o        o
```

`iptables -t nat -L` 이 nat 행만, 옵션 없는 `iptables -L` 이 filter 행만 보여주는 것이 이 격자 때문이다. 방화벽 판정(ACCEPT/DROP)은 filter 행에 살고 주소 변환은 nat 행에 산다. 두 행은 서로를 모른다.

nat 테이블에는 특성이 하나 더 있다. conntrack 이 커넥션을 추적하므로 규칙을 실제로 타는 것은 커넥션의 첫 패킷뿐이고, 이후 패킷과 응답 패킷은 추적 항목을 보고 자동으로 같은 변환을 받는다. 규칙 평가 비용은 커넥션당 한 번이다.

## 4. chain — 칸 안의 규칙 묶음

격자의 각 칸에는 hook 과 같은 이름의 빌트인 체인이 있다. 사용자 정의 체인은 거기서 jump 로 불려간다. 데몬이 설치하는 `DOCKER` 체인이 그 사용자 정의 체인이고, nat 테이블의 PREROUTING 과 OUTPUT 두 칸에서 불린다.

```
nat table
  PREROUTING chain
      -m addrtype --dst-type LOCAL  -j DOCKER
  OUTPUT chain
      ! -d 127.0.0.0/8 -m addrtype --dst-type LOCAL  -j DOCKER
  DOCKER chain
      -d 127.0.0.1/32 -p tcp --dport 5432  -j DNAT --to 172.18.0.x:5432
```

마지막 줄이 문서 첫머리의 compose 한 줄이 만들어내는 실물이다. `0.0.0.0` 바인딩이었다면 `-d 127.0.0.1/32` 조건 없이 규칙이 깔린다. 즉 바인딩 주소는 DNAT 규칙의 목적지 매칭 조건으로 번역된다.

OUTPUT 쪽 jump 규칙에 붙은 `! -d 127.0.0.0/8` 을 눈여겨볼 것. 호스트 자신이 루프백 주소로 보내는 패킷은 DOCKER 체인에 아예 도달하지 않는다. 6 절의 두 번째 시나리오가 여기서 갈린다.

이름이 겹치는 함정이 하나 있다. filter 테이블에도 `DOCKER` 라는 동명의 체인이 따로 있고 FORWARD 에서 불린다. 컨테이너 간 격리와 전달 허용을 담당하는 별개 체인이라 nat 쪽 DOCKER 와 혼동하면 안 된다. `iptables -L DOCKER` 처럼 테이블을 지정하지 않고 보면 filter 쪽이 나온다.

## 5. compose 문법 — 콜론 세 토막

```
HOST_IP : HOST_PORT : CONTAINER_PORT
```

`5432:5432` 처럼 두 토막만 쓰면 HOST_IP 가 생략된 것이고 생략의 기본값은 `0.0.0.0` 이다. 호스트에 붙은 모든 인터페이스에서 그 포트가 열린다. 앞에 `127.0.0.1:` 을 붙이면 바인딩이 루프백 인터페이스 하나로 좁혀진다.

선언 한 줄에 데몬은 두 가지를 각각 만든다.

1. nat 테이블 DOCKER 체인의 DNAT 규칙 한 줄
2. 해당 주소·포트에 bind 하고 listen 하는 docker-proxy 자식 프로세스

둘은 형제 관계지 부모-자식이 아니다. docker-proxy 는 자기 소켓만 들고 있고 규칙을 관리하지 않는다. 규칙을 쓰는 주체는 데몬이다. docker-proxy 를 kill 해도 규칙은 남아 있고 외부에서 들어오는 접속은 계속 성립한다.

## 6. 시나리오 둘

### 외부 호스트에서 들어오는 접속

같은 네트워크의 다른 장비에서 `psql -h <HOST_IP>` 를 친다. 목적지는 호스트의 실제 주소이고 포트는 5432 다.

```
SYN arrives at NIC
  -> PREROUTING: dst is a local address, jump to DOCKER
  -> DOCKER: rule requires -d 127.0.0.1, actual dst is HOST_IP  ==> no match
  -> no translation, dst still the host itself
  -> INPUT: nothing is listening on 0.0.0.0:5432
  -> kernel replies RST  ==> connection refused
```

docker-proxy 는 등장조차 하지 않는다. 막는 주체가 방화벽도 라우팅도 아니고, 변환 규칙이 매칭되지 않은 데다 받아줄 소켓도 없는 상태 그 자체다.

`0.0.0.0` 바인딩이었다면 DOCKER 체인의 규칙에 목적지 조건이 없어 매칭되고, 목적지가 컨테이너 IP 로 바뀌어 FORWARD 를 타고 브릿지 너머로 나간다. 커널 안에서 끝나므로 유저스페이스로 올라오지 않는다.

### 호스트 안에서 들어오는 접속

호스트에 로그인한 운영자가 `psql -h localhost` 를 친다.

```
packet starts at OUTPUT
  -> jump rule has ! -d 127.0.0.0/8, dst is 127.0.0.1  ==> skip DOCKER
  -> no DNAT
  -> docker-proxy, bound to 127.0.0.1:5432, accepts the connection
  -> docker-proxy opens a separate connection to the container and relays bytes
```

루프백 바인딩에서 docker-proxy 는 폴백이 아니라 유일한 경로다. 커널이 루프백 목적지를 DNAT 대상에서 명시적으로 제외하기 때문이다.

두 시나리오를 겹치면 이 한 줄의 실제 효과가 나온다 — 외부 유입은 규칙 매칭 실패로 거절되고, 호스트 로컬 접속만 프록시를 통해 살아남는다.

## 7. docker-proxy 의 역할

정상적인 외부 유입 경로에서 트래픽의 주역은 규칙이다. 데몬을 `--userland-proxy=false` 로 띄우면 docker-proxy 프로세스가 아예 생성되지 않는데도 외부에서의 포트 매핑은 그대로 동작한다. 그럼에도 프로세스가 기본으로 존재하는 이유는 셋이다.

| 역할 | 내용 |
|------|------|
| 루프백 경로 담당 | 6 절 두 번째 시나리오 — DNAT 가 제외하는 구간의 실제 수신자 |
| 포트 점유 | 실제 bind 로 인한 충돌 즉시 노출. 규칙만으로는 포트가 예약되지 않아 조용히 겹침 |
| 예외 경로 폴백 | 컨테이너가 자기 자신의 퍼블리싱된 포트로 접속하는 hairpin 등 규칙 한 줄로 안 풀리는 자리 |

한 줄로 나누면, 규칙은 외부 트래픽을 옮기고 프록시는 포트를 붙잡고 루프백과 예외를 줍는다.

## 8. 방화벽으로 막는 것이 왜 어긋나는가

호스트 방화벽(UFW · firewalld) 규칙은 filter 테이블의 INPUT 체인에 걸린다. 그런데 DNAT 를 거친 패킷은 목적지가 컨테이너 IP 로 바뀌어 있어 INPUT 이 아니라 FORWARD 로 빠진다. 3 절 격자에서 두 칸이 다른 행·다른 열에 있다.

결과적으로 방화벽에서 5432 를 막아 두어도 `0.0.0.0` 바인딩이면 외부에서 그대로 도달한다. 바인딩 주소를 좁히는 쪽은 DNAT 규칙 자체의 매칭 조건을 바꾸므로 우회할 자리가 없다. 노출을 막는 정공법이 방화벽이 아니라 바인딩 주소인 이유다.

FORWARD 에 직접 규칙을 넣어 통제하는 길도 있긴 하다. 데몬이 자기 규칙보다 앞서 평가되는 사용자용 체인을 하나 비워 두므로 거기에 넣어야 하는데, 바인딩으로 해결되는 사안에 굳이 쓸 이유는 없다.

## 9. 이 배포 구성의 바인딩 정책

배포 compose 는 컨테이너 6 개(웹 · 메시지 소비자 · 백그라운드 워커 · PostgreSQL · RabbitMQ · Redis)를 띄우고 그중 4 개가 포트를 퍼블리싱한다. 바인딩이 두 갈래로 갈린다.

| 포트 | 바인딩 | 이유 |
|------|--------|------|
| 웹 UI | 0.0.0.0 | 고객사 담당자가 브라우저로 접속하는 통로 |
| 메시지 브로커 AMQP | 0.0.0.0 | 고객사 호스트의 수집 에이전트가 밖에서 메트릭을 발행하는 통로 |
| 브로커 관리 UI | 127.0.0.1 | 운영자 전용. 호스트 로컬 또는 SSH 터널로만 |
| PostgreSQL | 127.0.0.1 | 운영자의 psql 전용 |
| Redis | 127.0.0.1 | 무인증 구성이라 노출 자체가 금지 |

갈림의 기준은 단순하다. 외부의 누군가가 그 포트로 먼저 접속을 걸어야 하는가. 웹과 AMQP 만 그렇고 나머지는 사람이 호스트 안에서 쓰는 통로다.

## 10. 애플리케이션 컨테이너는 이 매핑을 쓰지 않는다

웹 · 소비자 · 워커 세 컨테이너는 compose 가 만든 사용자 정의 브릿지 네트워크 안에서 DNS 이름(서비스명)으로 상대 컨테이너 IP 에 직접 붙는다. 배포 구성이 접속 호스트를 서비스명으로 고정 주입하는 것이 그래서다. 이 경로는 호스트 네트워크 스택도 DNAT 규칙도 거치지 않는다.

따라서 `ports:` 를 통째로 지워도 애플리케이션은 정상 동작한다. 세 줄의 루프백 퍼블리싱이 존재하는 이유는 프로그램이 아니라 사람이다.

```
   [ app container ] --- bridge network, DNS by service name ---> [ postgres container ]
                                  (no host stack, no DNAT)

   [ operator on host ] --- 127.0.0.1:5432 --- docker-proxy ----> [ postgres container ]
```

## 11. 포트 변수의 이중 용도 — 남아 있는 함정

퍼블리싱 선언에서 변수가 걸린 자리는 호스트 포트 한쪽뿐이고 컨테이너 포트는 리터럴이다. 컨테이너 안의 데몬은 이미지가 정한 포트에서 listen 하니 당연한 구조다.

문제는 같은 환경변수를 애플리케이션 설정도 읽어 접속 문자열에 넣는다는 점이다. 값을 바꾸면 두 층위가 어긋난다.

```
POSTGRES_PORT=5433

  host mapping   127.0.0.1:5433 -> container:5432      OK
  app DSN        postgres:5433                          container listens on 5432  ==> refused
```

브로커 · Redis 포트 변수도 같은 구조다. 이 변수들은 실질적으로 호스트 쪽 포트만 바꾸는 노브로 쓸 수 없고, 기본값을 유지한다는 전제에서만 성립한다. 호스트 포트만 옮기고 싶다면 퍼블리싱 왼쪽에 별도 변수를 두고 애플리케이션 설정용 변수와 분리해야 한다.

## 12. 용어

| 용어 | 의미 |
|------|------|
| netfilter | 리눅스 커널의 패킷 처리 프레임워크. iptables 가 조작하는 대상 |
| hook | 패킷 경로상의 5 개 지점. PREROUTING · INPUT · FORWARD · OUTPUT · POSTROUTING |
| table | 목적별 규칙 분류. nat 은 주소 변환, filter 는 통과 판정 |
| chain | (hook, table) 칸의 규칙 묶음. 빌트인은 hook 과 이름이 같다 |
| target | 규칙이 매칭됐을 때의 동작. DNAT · SNAT · ACCEPT · DROP · jump |
| DNAT | destination NAT. 목적지 주소·포트를 바꾼다. 퍼블리싱이 쓰는 변환 |
| conntrack | 커넥션 추적. 첫 패킷의 변환을 나머지 패킷에 자동 적용 |
| docker-proxy | 퍼블리싱마다 하나씩 뜨는 유저스페이스 TCP 프록시 |
| hairpin | 컨테이너가 자기 자신의 퍼블리싱된 포트로 접속하는 경로 |
