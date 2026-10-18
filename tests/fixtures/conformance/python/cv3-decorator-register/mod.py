ROUTES = {}


def route(path):
    def wrap(fn):
        ROUTES[path] = fn
        return fn

    return wrap


@route("/")
def index():
    pass


def work():
    pass


def apply(fn):
    fn()


def boot():
    apply(work)
