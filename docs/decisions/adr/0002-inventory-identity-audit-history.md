# 0002. Inventory 식별 보조값 이력 보존

## Status

Accepted

## Context

`agent_id`는 호스트 식별과 MQ routing의 단일 키다. `composite_id`와 `machine_id`는 현재 inventory 행에만 남으면 값이 바뀐 뒤 이전 값을 확인할 수 없다.

## Decision

`composite_id`와 `machine_id`를 `server_inventory_history` snapshot에 보존하고, 둘 중 하나가 바뀌면 history 행을 추가한다. 두 값은 식별키와 routing key로 사용하지 않는다.

## Consequences

새 history 행은 두 식별 보조값의 시간별 변화를 포함한다. migration 이전 history 행의 두 컬럼은 null이다.
