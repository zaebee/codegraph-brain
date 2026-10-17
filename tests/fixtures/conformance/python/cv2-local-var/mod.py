class C:
    def handle(self):
        pass

    def run(self):
        cb = self.handle
        cb()
