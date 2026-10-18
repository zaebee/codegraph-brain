class Board:
    def rank(self, x):
        return x

    def ordered(self, xs):
        return sorted(xs, key=self.rank)
