def even(x):
    return x % 2 == 0


def evens(xs):
    return list(filter(even, xs))
