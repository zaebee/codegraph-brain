# exp4 gold and task draw

The tools that build gold and draw the exp4 tasks for the pre-registration
`docs/lab-notes/2026-10-16-exp4-go-pre-registration.md`. Nothing here reads cgis output or runs
cgis. lima-vm/lima at `f47461ed7d03f300297e01fd659e9aefc6c228b9` was drawn first and is not
evaluable (below); golang/crypto at `0ae6809b7559060fe62abe65e26bd18b1fcaf6b7` is the repository
exp4 runs on. The tools take the repository as arguments (`<repo>.spec.json`,
`adjudications-<repo>.json`, `out/<repo>/`); lima's candidates and picks reproduce byte for byte.

| file | what it does |
|---|---|
| `main.go`, `fakecgo.go`, `go.mod` | `exp4cg`: go/ssa CHA and VTA over the root module's non-test packages, dumped as sorted TSV (format in `main.go`) |
| `goldmodel.py` | the gold model over a dump: edges, folding, adjudicated CHA-only edges, reverse closures |
| `helpers.py` | which packages are test helpers, by rule (see below) |
| `candidates.py` | eligibility filters F1 to F5 with counts, platform check, cells, interface families |
| `draw.py` | the seeded draw (`random.Random(4)`) |
| `build_tasks.py` | gold symbols with their spellings, and the task YAMLs |
| `adjudications-<repo>.json` | every CHA-only dispatch edge of the linux dump, with a verdict and a file:line reason |
| `<repo>.spec.json` | repo, sha, src_root, module path, and the nested modules that were deleted |
| `out/<repo>/` | the outputs: `dumps.sha256`, `helpers.json`, `candidates.json`, `picks.json`, `gold-<repo>.json` |

## Reproduce

Toolchain: go1.26.0 linux/amd64, golang.org/x/tools v0.50.0 (pinned in `go.mod`; v0.38 cannot
build SSA for go1.26's `new(expr)`, which lima uses), Python 3.12 with PyYAML.

```sh
# 1. the worktree: lima at the chosen sha, nested modules deleted (the draw used a clone of the
#    walk's shallow clone, /home/zaebee/bench-corpora/exp4-candidates/lima-vm__lima)
git clone https://github.com/lima-vm/lima /home/zaebee/bench-corpora/lima
git -C /home/zaebee/bench-corpora/lima checkout f47461ed7d03f300297e01fd659e9aefc6c228b9
rm -rf /home/zaebee/bench-corpora/lima/website /home/zaebee/bench-corpora/lima/hack/tools

# 2. the dumper
G=benchmarks/agent_ab/analysis/exp4/gold
(cd $G && GOFLAGS=-mod=mod go build -o /home/zaebee/bench-corpora/exp4cg .)

# 3. the dumps (O: anywhere outside /tmp; each is ~1.5 MB)
O=/home/zaebee/bench-corpora/exp4-lima-out; L=/home/zaebee/bench-corpora/lima; M=github.com/lima-vm/lima/v2
cd $L && export GOFLAGS=-mod=mod
GOOS=linux  GOARCH=amd64 CGO_ENABLED=1 ../exp4cg . $M           > $O/linux.tsv          # gold
GOOS=linux  GOARCH=amd64 CGO_ENABLED=1 ../exp4cg . $M -fakecgo  > $O/linux-fakecgo.tsv  # loader check
GOOS=darwin GOARCH=amd64 CGO_ENABLED=1 ../exp4cg . $M -fakecgo  > $O/darwin.tsv         # platform check
cmp $O/linux.tsv $O/linux-fakecgo.tsv   # must be identical (it is)
cd -

# 4. helpers, candidates, draw, gold and tasks
python3 -I $G/helpers.py $L $M > $G/out/lima/helpers.json
python3 -I $G/candidates.py $O/linux.tsv $O/darwin.tsv $G/adjudications-lima.json $G/out/lima/helpers.json $G/out/lima/candidates.json
python3 -I $G/draw.py $G/out/lima/candidates.json $G/out/lima/picks.json
python3 -I $G/build_tasks.py $O/linux.tsv $G/adjudications-lima.json $G/out/lima/helpers.json \
    $G/out/lima/picks.json $G/lima.spec.json benchmarks/agent_ab/tasks $G/out/lima/gold-lima.json
```

The dump hashes at the time of the draw are in `out/lima/dumps.sha256`. `linux.tsv` and
`linux-fakecgo.tsv` have the same hash.

Every step is deterministic. Two full runs gave byte-identical dumps, candidates, picks, gold and
YAMLs: map iteration in Go is sorted away, and the Python iterates sorted keys.

## The darwin build: `-fakecgo`

This host has no darwin C toolchain, so `go list -compiled` cannot run cgo for darwin. Code-Hex/vz,
which lima's vz driver needs, is all cgo. `-fakecgo` therefore lists packages without `-compiled`,
parses the Go and cgo files itself, and type-checks them with go/types' `FakeImportC`:

- A dependency that imports "C" gets no SSA bodies, only types. On darwin these are Code-Hex/vz,
  its objc package, runtime/cgo and rjeczalik/notify.
- An in-module function whose body names `C.` loses the statements that use C, or use a name such a
  statement declares. The function keeps its signature, so it is still a node and a method. lima
  has one such function: `LimaVzDriver.CaptureScreenshot` (`pkg/driver/vz/screenshot_darwin.go`).
  Its kept statements still call `canRunGUI`. The dropped statements call only `errors.New`, and
  stderr records this.

The loader is checked by running it for linux. Its dump is byte-identical to the standard
go/packages dump, even though net and os/user have no bodies in it.

## Rules the pre-registration left open (decided here, before the draw)

- **Test helpers (amendment 3).** A non-test package of the root module is a test helper when no
  non-test package imports it, under any of GOOS linux, darwin or windows, and its module-relative
  path has a segment that is `internal` or contains `test`. The first version also required a
  `_test.go` importer and had no path condition, which made every public leaf package of a library
  a helper. lima has none under either version. `*_test.go` files are never loaded.
- **Package initialisers.** The synthetic package initialiser (`pkg.init`) stands for package-level
  `var x = f()` declarations and for the import-order calls to other packages' initialisers. It is
  traversed, like a promoted wrapper, but it is never a gold symbol. A user-written `func init()`
  is keyed `pkg.init#N` and is a symbol. It is never a target.
- **Interfaces that are neither in-module nor stdlib** (third-party named interfaces) get VTA
  edges only, as stdlib and anonymous interfaces do. In lima no third-party interface dispatch
  reaches an in-module callee.
- **Goroutine launchers.** These are `sync.WaitGroup.Go`, `errgroup.Group.Go` and `TryGo`,
  `time.AfterFunc`, and any in-module function that starts the parameter with `go`. Only one site
  in lima matches: `time.AfterFunc(syncInterval, w.flush)` at `cmd/limactl/hostagent.go:174`.
- **Function-value references (amendment 2, replacing F3).** An `R` record is not walked: the
  referenced function is an entry point. The function that takes the value is allowed, not
  required; its own callers are not allowed. A target is no longer excluded for a reference. Under
  the original F3 the draw gave 0 iface and 3 static tasks on lima.
- **Closure roots (F4).** These are closure members, the target included, that have no non-helper
  caller. A root that is a method named Read, Write, Close, ReadFrom, WriteTo, ReadAt, String or
  Error makes the target ineligible.
- **Platform check (F5).** The required closure is compared under GOOS linux and darwin as a set of
  (symbol, file) pairs. A target absent from the darwin build fails. darwin-only CHA edges, such as
  `Driver -> vz.LimaVzDriver`, have no rules and are accepted. That can only drop more targets.
- **iface family.** A target's family is the in-module interface of its direct dispatch caller,
  the first in sorted order if there are several. Within a family, each pick needs a method name
  and a receiver type that no earlier pick has. The text reads "distinct method names and
  receiver types", which is stricter than the kafka draft. That draft waived distinct receivers
  for single-type families.
- **Static draw seed.** "The same seed" is read as a fresh `random.Random(4)`, not as continuing
  the generator used for the iface draw.
- **Draw surplus and shortfall (amendment 2).** More than 12 iface picks: a fresh
  `random.Random(4).sample` keeps 12. Fewer than 8: the primary gate is not evaluable on the
  repository (`iface_evaluable: false` in `picks.json`), and the walk continues.
- **Gold files.** These are the files of the required symbols. The target's own file goes in
  `allowed_files` when no required symbol lives there.

## Symbol spellings

The harness folds `(*T).M`, `func (r *T) M`, `/`, `::` and a `.go` path suffix. It also matches
gold on whole dotted components, so any longer qualified name that contains a gold spelling is
found. Each symbol lists:

- `dir.Rest`, `pkgpath.Rest` and `file:Rest`;
- `pkgname.Rest`, when the package name differs from its directory (`main.newApp`);
- the bare name, only when no other function or method in the module has it.

`main` is spelled `limactl.main`, `cmd/limactl.main`, `main.main` and `cmd/limactl/main.go:main`.
`limactl.main` is the form cgis itself produces (`cmd.limactl.main`). The trade-off: a
path-qualified answer for another symbol in `main.go` also matches it, because
`cmd/limactl/main.go:newApp` folds to `cmd.limactl.main.newApp`. A probe confirms this. It is kept
because dropping that spelling would score cgis's own spelling as a miss.

## lima's result under amendment 2

`out/lima/picks.json`: 7 iface picks (families AutoStartManager, CopyTool, Driver ×2, DriverClient,
GuestServiceClient, ImageDiskManager) and 6 static. 7 is below 8, so lima is not evaluable and no
task YAMLs were written for it. Driver gives 2 of its 3, and DriverClient 1 of 3, because every
member of DriverClient's family is a method of the one generated `driverClient` type.

## golang/crypto under amendment 2

Worktree: a clone of the walk's shallow clone at `0ae6809b`, with eight nested modules deleted
(`crypto.spec.json`): seven `_asm/` generator modules (argon2, blake2b/AVX2, blake2b/standard,
blake2s, chacha20poly1305, internal/poly1305, salsa20/salsa) and `x509roots/fallback/`. The task
YAMLs list them as `delete_paths`. The commands are the lima ones with `crypto`,
`golang.org/x/crypto`, `/home/zaebee/bench-corpora/crypto` and `/home/zaebee/bench-corpora/exp4-crypto-out`
substituted. x/crypto has no cgo: `linux.tsv` and `linux-fakecgo.tsv` are identical, and
`darwin.tsv` differs from them only in its header line, so the platform check drops nothing.

**Adjudication in a library** (decided before the draw). lima's interfaces were filled by lima's
own code; x/crypto's are mostly filled by its callers. VTA is sound for flows inside the module, so
a CHA-only edge can only be real through code outside it. An edge is accepted when the site's value
comes from a caller of the exported API (an argument, an exported field, a callback the caller
supplies, or the caller's own implementation of an exported interface) and that caller can hold a
value of that type without reflect or unsafe. Everything else is rejected. 914 CHA-only edges: 350
accepted, 564 rejected. Rules can now name several receivers (`types`) and restrict to callers
(`callers`), because every wrapper body has the site `:0`; lima's file uses neither.

**Test helpers.** The first helper rule marked 12 packages, ten of them production API
(`ssh/agent`, `sha3`, `acme/autocert`, ...). Amendment 3 replaced it. The new rule marks 4:
`acme/autocert/internal/acmetest`, `acme/internal/acmeprobe`, `internal/testenv` and `ssh/test`.
The first draw's record is kept in `out/crypto/picks-before-amendment3.json` and
`helpers-before-amendment3.json`.

**Result.** `out/crypto/picks.json`: 19 iface picks from 14 families, cut to 12 by the surplus rule
(`iface_evaluable: true`), and 6 static picks from 206 candidates in 42 files. Filters: F1 1,292,
F2 379, F4 347, F5 347 (79 iface, 206 static, 62 neither). Kept iface picks: acme/autocert.Cache,
pkcs12.decryptable, pkcs12.pbeCipher ×2, ssh.AuthMethod, ssh.Channel, ssh.MultiAlgorithmSigner,
ssh.NewChannel, ssh.PublicKey, ssh.kexAlgorithm, ssh.packetCipher and ssh/agent.ExtendedAgent. The
18 YAMLs are `benchmarks/agent_ab/tasks/go-exp4-crypto-*.yaml`.
