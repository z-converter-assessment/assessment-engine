학습 자료 초안이며 자유롭게 삭제할 수 있다.

# functools.lru_cache와 Settings factory

`functools.lru_cache`는 함수 호출 결과를 인자로 식별해 메모리에 보관하는 decorator다. 같은 인자로 다시 호출하면 함수를 실행하지 않고 보관한 결과를 반환한다.

## 구성 요소 4개

- 함수: 원래 계산이나 객체 생성을 수행하는 대상
- cache key: 위치 인자와 키워드 인자로 만든 호출 식별자
- cache value: 해당 호출의 반환값
- maxsize: 보관 가능한 결과 개수 상한

## LRU 동작

LRU는 Least Recently Used의 약자다. 최근 사용한 결과는 유지하고, 캐시가 가득 찬 상태에서 새 key가 들어오면 가장 오래 사용하지 않은 결과를 버린다.

```python
from functools import lru_cache


@lru_cache(maxsize=2)
def square(value: int) -> int:
    print("calculate")
    return value * value


square(2)  # calculate 출력. cache: [2].
square(3)  # calculate 출력. cache: [2, 3].
square(2)  # 출력 없음. 2가 가장 최근 사용 항목이 됨. cache: [3, 2].
square(4)  # calculate 출력. 가장 오래된 3을 제거. cache: [2, 4].
```

위 순서에서 대괄호의 오른쪽은 가장 최근 사용한 항목이다.

cache key에 쓰이는 인자는 hashable이어야 한다. `int`, `str`, `tuple`은 보통 가능하고 `list`, `dict`는 기본적으로 불가능하다.

## 인자가 없는 Settings factory

```python
from functools import cache


@cache
def get_consumer_settings() -> ConsumerSettings:
    return ConsumerSettings()
```

인자가 없는 함수는 호출 형태가 하나뿐이다. 첫 호출이 `ConsumerSettings` 인스턴스를 만들고, 이후 호출은 같은 인스턴스를 반환한다.

```python
get_consumer_settings() is get_consumer_settings()
# True.
```

이는 프로세스 안에서만 성립한다. web 컨테이너와 consumer 컨테이너는 서로 다른 Python 프로세스이므로 각자 별도 캐시와 Settings 인스턴스를 가진다.

## Settings factory에 쓰는 이유

`ConsumerSettings()`를 module-level 변수로 만들면 import 순간 환경변수와 secret을 읽고 검증한다. 비밀번호가 아직 없는 단순 import나 테스트 수집도 실패할 수 있다.

`get_consumer_settings()` factory는 첫 실제 사용 시점까지 인스턴스 생성을 늦춘다. `@cache`는 그 뒤 같은 프로세스에서 설정 파일 파싱과 secret 검증을 반복하지 않게 한다.

생성이 예외를 내면 반환값이 없으므로 캐시되지 않는다. 다음 호출은 다시 `ConsumerSettings()` 생성을 시도한다.

## cache_clear와 cache_info

`lru_cache`가 감싼 함수에는 관리 메서드가 생긴다.

```python
get_consumer_settings.cache_info()
get_consumer_settings.cache_clear()
```

`cache_info()`는 hit, miss, 현재 보관 개수 같은 통계를 준다. `cache_clear()`는 보관된 결과를 비운다. 테스트가 환경변수를 바꾼 뒤 새 Settings를 검증해야 할 때 사용한다.

## 전역 변수와의 차이

```python
settings = ConsumerSettings()
```

전역 변수는 import 시점에 즉시 생성된다. 반면 cached factory는 지연 생성이고, 반환 타입과 생성 경로를 함수 이름으로 드러낸다.

```python
@cache
def get_consumer_settings() -> ConsumerSettings:
    return ConsumerSettings()
```

Settings factory의 목적은 지연 생성이고, `@cache`의 목적은 성공적으로 생성한 인스턴스를 프로세스 안에서 재사용하는 것이다.
