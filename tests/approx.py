from typing import Any

import pytest


def approx(expected: float, *, abs: float | None = None, rel: float | None = None) -> Any:  # noqa: A002
    return pytest.approx(expected, abs=abs, rel=rel)
