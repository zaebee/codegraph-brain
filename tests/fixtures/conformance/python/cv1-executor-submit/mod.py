from concurrent.futures import ThreadPoolExecutor


def work(x=None):
    pass


def fan_out(xs):
    with ThreadPoolExecutor() as ex:
        ex.submit(work)
        list(ex.map(work, xs))
