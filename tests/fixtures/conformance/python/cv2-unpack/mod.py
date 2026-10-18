class C:
    def a(self):
        pass

    def b(self):
        pass

    def parse(self, req):
        return self.a, req

    def run(self, req):
        first, second = self.a, self.b
        second()

    def dispatch(self, req):
        handler, args = self.parse(req)
        handler()
