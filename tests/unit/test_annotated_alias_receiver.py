"""A parameter typed with an `Annotated[T, Depends(...)]` alias holds a `T` (#550).

On owner-api a route took `top_up_service: TopUpServiceDep`, and its call
`top_up_service.get_status()` stayed unresolved: the parameter's recorded type
was the alias, which has no methods. Impact on `TopUpService.get_status` then
stopped short of the HTTP route.
"""

from pathlib import Path

import pytest

from cgis.api.mcp_server import cgis_ingest
from cgis.query.engine import QueryEngine
from cgis.storage.sqlite_store import SQLiteStore

_SERVICE = """
class Service:
    def run(self):
        pass


class Other:
    def run(self):
        pass
"""

_DEPS = """
from typing import Annotated
from fastapi import Depends
from service import Other, Service


def get_service():
    return Service()


def get_other():
    # Uses `Other` here, so switching the alias to it changes nothing else about
    # deps.py: an imported-but-unused name counts as a re-export (the signature).
    return Other()


def get_user():
    pass


ServiceDep = Annotated[Service, Depends(get_service)]
CurrentUser = Depends(get_user)
"""

_ROUTES = """
from typing import Annotated
from fastapi import Depends
from deps import ServiceDep, CurrentUser, get_service
from service import Service


async def via_alias(svc: ServiceDep):
    svc.run()


async def inline(svc: Annotated[Service, Depends(get_service)]):
    svc.run()


async def plain_depends(user=CurrentUser):
    user.run()
"""


def _write(root: Path, deps: str = _DEPS) -> None:
    (root / "service.py").write_text(_SERVICE, encoding="utf-8")
    (root / "deps.py").write_text(deps, encoding="utf-8")
    (root / "routes.py").write_text(_ROUTES, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """The three-file repo, ingested."""
    _write(tmp_path)
    db = str(tmp_path / "graph.db")
    cgis_ingest(str(tmp_path), db)
    return tmp_path, db


def _calls_from(db: str, source: str) -> set[str]:
    """Targets of the CALLS edges leaving `source`."""
    with SQLiteStore(db) as store:
        return {e.target for e in store.get_outgoing_edges(source) if e.type.value == "CALLS"}


def test_a_call_on_an_alias_typed_parameter_resolves_to_the_annotated_type(
    repo: tuple[Path, str],
) -> None:
    _, db = repo
    assert "service.Service.run" in _calls_from(db, "routes.via_alias")


def test_the_inline_form_resolves_too(repo: tuple[Path, str]) -> None:
    _, db = repo
    assert "service.Service.run" in _calls_from(db, "routes.inline")


def test_the_alias_records_the_type_it_annotates(repo: tuple[Path, str]) -> None:
    _, db = repo
    with SQLiteStore(db) as store:
        alias = store.get_node("deps.ServiceDep")
        bare = store.get_node("deps.CurrentUser")

    assert alias is not None
    assert alias.metadata.get("annotated_type") == "service.Service"
    assert bare is not None, "a Depends(...) alias without Annotated is still a node (#161)"
    assert "annotated_type" not in bare.metadata


def test_impact_reaches_the_route(repo: tuple[Path, str]) -> None:
    """The issue's acceptance, in miniature: impact on the method lists the route."""
    _, db = repo
    with SQLiteStore(db) as store:
        nodes, _ = QueryEngine(store).get_impact_graph("service.Service.run", max_depth=2)

    assert "routes.via_alias" in {n.id for n in nodes}


def test_retyping_the_alias_reresolves_the_route_on_an_incremental_ingest(
    repo: tuple[Path, str],
) -> None:
    """Only deps.py changes; routes.py's calls must follow the alias's new type.

    routes.py is not rewritten: a touched file is re-resolved anyway, and the
    test would pass without the alias's type in the cross-file signature.
    """
    root, db = repo
    # Nothing else in deps.py moves — not its imports, not its names — so only the
    # alias's recorded type can tell the pipeline that routes.py needs re-resolving.
    (root / "deps.py").write_text(_DEPS.replace("Annotated[Service,", "Annotated[Other,"))
    cgis_ingest(str(root), db)

    targets = _calls_from(db, "routes.via_alias")
    assert "service.Other.run" in targets
    assert "service.Service.run" not in targets
