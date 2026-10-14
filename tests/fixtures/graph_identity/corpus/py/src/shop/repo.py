from abc import ABC, abstractmethod


class Repo(ABC):
    @abstractmethod
    def save(self, item: str) -> None: ...


class SqlRepo(Repo):
    def save(self, item: str) -> None:
        print(item)
