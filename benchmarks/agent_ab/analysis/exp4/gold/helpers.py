"""Which packages of the root module are test helpers, by rule.

A test helper is a non-test package of the root module that no non-test package imports, under
any of GOOS=linux, darwin and windows (GOARCH=amd64), and whose path relative to the module has a
segment that is `internal` or contains `test` (amendment 3: a public leaf package of a library is
imported only by tests, yet it is production code).
Test files themselves (`*_test.go`) are never loaded by exp4cg, so they need no rule.

Usage: python3 -I helpers.py <moduleDir> <modulePath> > helpers.json
"""

import json
import os
import subprocess
import sys

FMT = "{{.ImportPath}}|{{join .Imports \" \"}}|{{join .TestImports \" \"}}|{{join .XTestImports \" \"}}"


def main() -> None:
    mod_dir, mod = sys.argv[1], sys.argv[2]
    prod: dict[str, set[str]] = {}
    test: dict[str, set[str]] = {}
    pkgs: set[str] = set()
    for goos in ("linux", "darwin", "windows"):
        env = dict(os.environ, GOOS=goos, GOARCH="amd64", GOFLAGS="-mod=mod")
        out = subprocess.run(["go", "list", "-f", FMT, "./..."], cwd=mod_dir, env=env,
                             capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            p, imps, timps, ximps = line.split("|")
            pkgs.add(p)
            for q in imps.split():
                prod.setdefault(q, set()).add(f"{goos}:{p}")
            for q in (timps + " " + ximps).split():
                test.setdefault(q, set()).add(f"{goos}:{p}")
    rel = lambda p: p.removeprefix(mod + "/")  # noqa: E731
    def helper_path(p: str) -> bool:
        return any(seg == "internal" or "test" in seg for seg in rel(p).split("/"))

    helpers = sorted(rel(p) for p in pkgs if not prod.get(p) and helper_path(p))
    unimported = sorted(rel(p) for p in pkgs if not prod.get(p) and not test.get(p))
    json.dump({"rule": __doc__.split("\n\n")[1].replace("\n", " "),
               "helper_packages": helpers,
               "no_importers_at_all": unimported}, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
