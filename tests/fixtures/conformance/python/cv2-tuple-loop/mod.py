class C:
    def a(self):
        pass

    def b(self):
        pass

    def run(self):
        for step in (self.a, self.b):
            step()
