from concurrent.futures import ProcessPoolExecutor


def work(x=None):
    pass


def fan_out(xs):
    with ProcessPoolExecutor() as ex:
        ex.submit(work)
        list(ex.map(work, xs))
