import functools
from functools import partial


def scale(k, x):
    return k * x


def doubled(xs):
    return list(map(partial(scale, 2), xs))


def tripled(xs):
    return list(map(functools.partial(scale, 3), xs))
