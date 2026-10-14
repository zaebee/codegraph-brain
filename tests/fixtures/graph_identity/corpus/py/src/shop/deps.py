from typing import Annotated

from fastapi import Depends

from .repo import Repo, SqlRepo


def get_repo() -> Repo:
    return SqlRepo()


RepoDep = Annotated[Repo, Depends(get_repo)]
