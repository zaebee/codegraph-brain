import multiprocessing


def job():
    pass


def spawn():
    multiprocessing.Process(target=job).start()
