import threading


def job():
    pass


def other():
    pass


def right():
    threading.Thread(None, job).start()


def wrong():
    threading.Thread(other).start()
