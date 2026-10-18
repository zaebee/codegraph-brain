def helper(fn):
    fn()


def run(fn):
    helper(fn)


def work():
    pass


def outer():
    run(work)
