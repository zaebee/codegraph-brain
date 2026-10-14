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
import typing
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
QualifiedDep = typing.Annotated[Service, Depends(get_service)]
ForwardDep = Annotated["Service", Depends(get_service)]
ListDep = Annotated[list[Service], Depends(get_service)]
CommentedDep = Annotated[
    # the service, injected
    Service,
    Depends(get_service),
]
"""

_ROUTES = """
from typing import Annotated
from fastapi import Depends
from deps import CommentedDep, ForwardDep, QualifiedDep, ServiceDep, CurrentUser, get_service
from service import Service


async def via_alias(svc: ServiceDep):
    svc.run()


async def inline(svc: Annotated[Service, Depends(get_service)]):
    svc.run()


async def plain_depends(user=CurrentUser):
    user.run()


async def qualified(svc: QualifiedDep):
    svc.run()


async def forward(svc: ForwardDep):
    svc.run()


async def commented(svc: CommentedDep):
    svc.run()


class Holder:
    def __init__(self, svc: ServiceDep):
        self.svc = svc

    def go(self):
        self.svc.run()
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


@pytest.mark.parametrize("route", ["routes.qualified", "routes.forward", "routes.commented"])
def test_qualified_forward_and_commented_aliases_resolve(
    repo: tuple[Path, str], route: str
) -> None:
    _, db = repo
    assert "service.Service.run" in _calls_from(db, route)


def test_a_builtin_container_records_no_type(repo: tuple[Path, str]) -> None:
    """`Annotated[list[T], ...]` cleans to `list`, which would read as `deps.list`."""
    _, db = repo
    with SQLiteStore(db) as store:
        alias = store.get_node("deps.ListDep")
    assert alias is not None
    assert "annotated_type" not in alias.metadata


def test_an_attribute_assigned_from_an_alias_typed_parameter_resolves(
    repo: tuple[Path, str],
) -> None:
    """The neighbouring entry: `self.svc = svc` with `svc: ServiceDep` (#550 review)."""
    _, db = repo
    assert "service.Service.run" in _calls_from(db, "routes.Holder.go")


def test_an_import_path_prefixed_above_the_graph_resolves(tmp_path: Path) -> None:
    """owner-api's layout: ingested from `app/`, importing `from app.svc...`.

    The alias's recorded type carries the `app.` prefix the node ids lack, and
    only `map_to_node_fqn` reconciles the two.
    """
    root = tmp_path / "app"
    (root / "svc").mkdir(parents=True)
    (root / "api").mkdir()
    (root / "svc" / "service.py").write_text(_SERVICE, encoding="utf-8")
    (root / "svc" / "deps.py").write_text(
        "from typing import Annotated\n"
        "from fastapi import Depends\n"
        "from app.svc.service import Service\n\n"
        "def get_service():\n    return Service()\n\n"
        "ServiceDep = Annotated[Service, Depends(get_service)]\n",
        encoding="utf-8",
    )
    (root / "api" / "routes.py").write_text(
        "from app.svc.deps import ServiceDep\n\nasync def route(svc: ServiceDep):\n    svc.run()\n",
        encoding="utf-8",
    )
    db = str(tmp_path / "graph.db")
    cgis_ingest(str(root), db)

    assert "svc.service.Service.run" in _calls_from(db, "api.routes.route")
