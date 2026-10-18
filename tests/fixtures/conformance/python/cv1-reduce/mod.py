import functools
from functools import reduce


def add(a, b):
    return a + b


def total(xs):
    return functools.reduce(add, xs)


def total_imported(xs):
    return reduce(add, xs)
