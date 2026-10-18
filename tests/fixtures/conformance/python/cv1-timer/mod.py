import threading


def tick():
    pass


def later():
    threading.Timer(1.0, tick).start()


def later_by_name():
    threading.Timer(1.0, function=tick).start()
