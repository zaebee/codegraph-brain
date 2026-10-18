class C:
    def a(self):
        pass

    def b(self):
        pass

    def run(self):
        cb = self.a
        cb = self.b
        cb()
