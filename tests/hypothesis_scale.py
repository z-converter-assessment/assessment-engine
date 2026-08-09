import os

_SCALE = float(os.getenv("HYPOTHESIS_SCALE", "1"))
_FLOOR = 50


def examples(declared: int) -> int:
    return max(_FLOOR, int(declared * _SCALE))
