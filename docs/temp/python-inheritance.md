# Python 상속과 Protocol

학습 자료 초안이며 자유롭게 삭제할 수 있다.

## 클래스 생성

Python 클래스가 `__init__`을 정의하지 않으면 부모 클래스의 `__init__`을 사용한다. 부모를 지정하지 않은 클래스는 `object`를 상속하므로 인자 없는 생성만 가능하다.

```python
class Parent:
    def __init__(self, name: str) -> None:
        self.name = name


class Child(Parent):
    pass


child = Child("min")
```

자식이 `__init__`을 정의하면 부모 초기화가 자동으로 호출되지 않는다. 부모 상태가 필요하면 `super().__init__(...)`을 호출한다.

```python
class Child(Parent):
    def __init__(self, name: str, level: int) -> None:
        super().__init__(name)
        self.level = level
```

Java는 생성자를 상속하지 않는다. 자식에 생성자가 없으면 Java가 기본 생성자를 만들고 `super()`를 호출한다. 부모에 인자 없는 생성자가 없으면 자식이 부모 생성자 호출을 명시해야 한다. C는 언어 차원의 클래스와 생성자가 없다.

## 상속을 선택하는 기준

상속은 자식이 부모의 한 종류일 때 사용한다. 부모가 쓰이는 모든 위치에 자식을 넣어도 계약과 의미가 유지돼야 한다.

- 상태와 동작을 함께 물려받아야 함: 상속
- 다른 객체의 기능을 사용하기만 함: composition
- 호출 가능한 메서드 형태만 필요함: Protocol

단순 코드 재사용만을 위해 상속하지 않는다. 다중 상속은 작은 mixin 조합으로 제한한다. 다중 상속에서는 Method Resolution Order, MRO가 초기화와 메서드 탐색 순서를 결정한다.

## Protocol과 구조적 타이핑

`Protocol`은 구현체가 제공해야 할 메서드 계약을 선언한다.

```python
class CollectRepository(Protocol):
    async def upsert_server(self, data: ServerInventoryCreate) -> int: ...
```

구현체는 `CollectRepository`를 명시적으로 상속하지 않아도 된다. 메서드 이름, 인자 타입, 반환 타입이 계약과 맞으면 정적 타입 검사기가 구현체로 인정한다.

```python
class SqlCollectRepository:
    async def upsert_server(self, data: ServerInventoryCreate) -> int: ...
```

이 방식을 구조적 타이핑이라고 한다. Python의 `Protocol`은 정적 타입 검사기에서 계약 호환성을 확인한다.

현재 프로젝트에서는 `CollectRepository`가 계약이고, `SqlCollectRepository`가 PostgreSQL 구현이며, `InMemoryCollectRepository`가 테스트용 메모리 구현이다.

## dataclass와 Pydantic

`dataclass`는 내부 DTO와 처리 결과처럼 데이터가 중심인 객체에 사용한다. 생성자, 문자열 표현, 동등성 비교를 자동 생성한다. 단순 getter와 setter를 만들기 위한 기능은 아니다.

Pydantic 모델은 HTTP, RabbitMQ, 환경변수처럼 외부에서 들어오는 데이터를 검증할 때 사용한다. `dataclass`는 검증을 수행하지 않는다.
