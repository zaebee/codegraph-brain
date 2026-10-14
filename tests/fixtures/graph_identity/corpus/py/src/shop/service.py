from .deps import RepoDep
from .repo import Repo

__all__ = ["OrderService", "place"]


class OrderService:
    def __init__(self, repo: RepoDep) -> None:
        self.repo = repo

    def place(self, item: str) -> None:
        self.repo.save(item)


def place(repo: Repo, item: str) -> None:
    repo.save(item)
