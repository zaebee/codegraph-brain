def handler():
    pass


def real():
    pass


def run(a, b):
    a()
    b()


def outer(handler):
    run(handler, real)
