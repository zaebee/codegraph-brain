class Runner:
    def apply(self, label, fn):
        return fn()

    def work(self):
        pass

    def go(self):
        self.apply("w", self.work)
