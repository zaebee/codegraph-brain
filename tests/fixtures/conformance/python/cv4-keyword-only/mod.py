def run(*, cb):
    cb()


def work():
    pass


def go():
    run(cb=work)
