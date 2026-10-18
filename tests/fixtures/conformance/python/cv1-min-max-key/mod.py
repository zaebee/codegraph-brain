def rank(x):
    return x


def size(x):
    return x


def extremes(xs):
    return min(xs, key=rank), max(xs, key=size)
