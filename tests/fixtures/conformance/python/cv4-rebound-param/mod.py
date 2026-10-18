def run(cb, fn):
    cb = None
    fn()
    return cb


def work():
    pass


def other():
    pass


def outer():
    run(work, other)
