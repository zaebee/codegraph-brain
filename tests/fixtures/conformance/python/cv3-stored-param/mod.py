class Bus:
    def __init__(self):
        self._handlers = []

    def subscribe(self, fn):
        self._handlers.append(fn)

    def run_now(self, fn):
        fn()


def on_event():
    pass


def work():
    pass


def wire(bus: Bus):
    bus.subscribe(on_event)
    bus.run_now(work)
