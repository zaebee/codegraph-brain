class C:
    def a(self):
        pass

    def b(self):
        pass

    def run(self, flag):
        cb = self.a
        if flag:
            cb = self.b
        cb()
