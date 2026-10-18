class C:
    def handle(self):
        pass

    def run(self):
        if (cb := self.handle):
            cb()
