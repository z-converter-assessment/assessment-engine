"""alembic CLI 진입점 — 설정 파일 위치를 패키지가 스스로 찾는다.

`alembic` 명령은 cwd 의 `alembic.ini` 를 찾는데 이 저장소는 설정을 패키지 안에 둔다(마이그레이션을
실행하는 주체가 개발자 셸이 아니라 배포된 컨테이너라 이미지에 함께 담겨야 한다).

경로를 이미지 `ENV` 로 박으면 그 값에 파이썬 minor 버전이 들어가 베이스 이미지를 올릴 때마다 손으로
따라가야 한다. 심링크로 우회하는 것도 안 된다 — `script_location = %(here)s/migrations` 의 `here` 는
alembic 이 받은 경로의 디렉토리이고 심링크를 풀지 않으므로 링크가 놓인 자리에서 리비전을 찾는다.

여기서 부르면 `import assessment_engine` 이 해석된 자리가 곧 설정 위치다. dev 는 bind mount 소스,
prod 는 이미지 안 설치본을 각각 알아서 가리킨다.
"""

import sys
from importlib import resources

from alembic.config import main


def run() -> None:
    config = resources.files(__package__) / "_alembic.ini"
    main(argv=["-c", str(config), *sys.argv[1:]])


if __name__ == "__main__":
    run()
