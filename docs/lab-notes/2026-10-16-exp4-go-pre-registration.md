# Exp4: does cgis help an agent answer impact questions in Go? Pre-registration

Date: 2026-10-16. Committed before the repository is chosen, before any gold, and before any session.
cgis is main at 3711351, which includes the Go work exp4 waited for: interface dispatch (#647, #649,
#651), typed receivers, bindings, block scope and chained calls (#656, #660, #662, which close #643),
call lines in the coverage note (#665), and one build configuration (#667).

## Question and decision

Exp3 found that forced cgis cuts the cost of a Python impact question to ×0.63 at equal recall
(#635). Does that carry over to Go, where callers often arrive through interface dispatch?

**The decision is fixed now.**
- **If the primary gate passes,** the README and the MCP server instructions add Go to the languages
  cgis answers impact questions for.
- **If it fails,** they say Go is supported for structure and metrics only. The coverage line on a Go
  graph then tells the agent to confirm callers by reading the code.

## Repository: chosen blind, by a seeded rule

Two kinds of repository are out:
- **Tuned on:** hashicorp/raft, prometheus/client_golang and segmentio/kafka-go. The last was a held-out
  candidate, but its cgis-vs-gold misses were seen during #643.
- **Looked at during earlier selection:** nats.go, memberlist, go-kit/kit, zap and bbolt.

1. **Pool.** Three GitHub searches, run once with their output saved:
   `language:Go stars:1000..30000 pushed:>=2025-10-16 archived:false fork:false license:<L>`, with L =
   `mit`, `apache-2.0` and `bsd-3-clause`. Each query keeps its first 100 results by stars. The pool is
   the union of the three result sets, minus the exclusions above, sorted by `full_name`.
2. **Order.** `random.Random(20261016).shuffle(pool)`.
3. **Walk.** Candidates are taken in that order. Each is shallow-cloned at its default branch and its
   sha is recorded. The first one that passes every check is chosen. Every check result is logged,
   including the failures. No cgis command runs on a candidate before the choice.
4. **Checks**, all mechanical, using the go1.26 toolchain on linux/amd64:
   - a `go.mod` at the repository root;
   - 15,000 to 60,000 lines of non-test Go in the root module (nested modules not counted);
   - `go build ./...` succeeds in the root module;
   - type parameters in at most 10% of non-test files;
   - at least 10 in-module interfaces with at least 2 in-module implementations each, counted with
     go/ssa CHA over non-test packages;
   - not a code generator's output tree: at most 30% of non-test lines in files whose header says
     `Code generated ... DO NOT EDIT`.
5. **Nested modules.** Directories with their own `go.mod` are deleted from the worktree before both
   arms run, and gold never includes them (#655).

## Gold

The model is the one drafted for kafka-go:
- **Graphs.** go/ssa CHA and VTA over the root module's non-test packages.
- **Static calls.** Static calls are edges, and so are `go` and `defer` calls.
- **Closures.** Closures, bound methods and thunks fold into the function that encloses them.
- **Synthetic wrappers.** Promoted-method wrappers are traversed but are never gold symbols.
- **Interface dispatch.** A call on an in-module interface takes the CHA implementations. Each edge that
  CHA has and VTA lacks is adjudicated by reading the code. A call on a stdlib or anonymous interface
  takes only VTA edges.
- **Function values.** A call through a function value is not an edge. A method value handed to a
  goroutine launcher counts as a call by the function that hands it over. A target whose closure holds
  any other function-value reference is not selected.
- **Required vs allowed.** Required: every non-test, non-helper function in the target's transitive
  caller closure. Allowed: test helpers and the target itself.

**Gold is validated before it is frozen.** One pilot session per task in each arm is run. Any symbol a
pilot names that gold lacks, or that gold has and both pilots miss, is adjudicated against the code.
Gold is corrected and frozen before the main run. Pilot sessions are not scored. Every gold round in
exp1–exp3 had errors that a pilot would have caught.

## Tasks: drawn by rule, seeded

Eligible targets:
- a concrete, non-test, non-synthetic function or method;
- gold of 4 to 25 symbols spread over 2 to 8 files;
- no function-value reference in the caller closure;
- no closure root that is a stdlib-protocol method (`Read`, `Write`, `Close`, `ReadFrom`, `WriteTo`,
  `ReadAt`, `String`, `Error`).

The tasks fall into two cells:
- **iface (12 tasks).** The target has a direct caller through interface dispatch, and at least one
  required caller is reachable only through dispatch. They are drawn with `random.Random(4)`, one
  target per in-module interface family. Families with 6 or more candidates give up to 3 targets, with
  distinct method names and receiver types.
- **static (6 tasks).** No dispatch edge anywhere in the closure. They are drawn with the same seed
  from 6 distinct files.

The question is the exp1–exp3 wording: "Which functions and methods can end up calling `<target>`?
List every one, production code only."

## Arms and runs

- **Arms.** `control` has no cgis. `cgis-forced` uses the exp3 guard: Read, Grep, Glob and Bash are
  refused until the session makes one cgis call. The graph comes from a full `cgis ingest` of the
  worktree, not an incremental one, because of #666.
- **Runs.** 3 per task per arm: 18 tasks × 2 arms × 3 = 108 sessions.
  - Model `claude-sonnet-5-5`, `--max-budget-usd 2.0`, organisation API key.
  - Every transcript must show `apiKeySource: ANTHROPIC_API_KEY`.
  - The two arms run interleaved by task, in the same hours.
- **Before the run.** The count of #661 collisions on the chosen graph is reported: unresolved
  edges whose raw name equals an INTERNAL function or method id.
- **Budget.** The 108 main sessions cost about $11–14, and the pilot about $2. About $57 remains of $90.

## Gates

Per task:
- **cost ratio:** median cost (cgis-forced) / median cost (control);
- **recall difference:** median recall (cgis-forced) − median recall (control).

1. **Primary (iface, n = 12).** The Go pack **helps** if the median cost ratio is ≤ 0.80 **and** the
   median recall difference is ≥ −0.02. Otherwise it **does not help**, and the decision above
   applies. A bootstrap 95% CI over tasks is reported, and the point estimate decides.
2. **Secondary.** Descriptive only: the same two figures on the static cell and on all 18 tasks;
   median precision per arm; cgis calls, files read, turns and wall time per session; whether the
   answer used cgis output.
3. **Known-miss check.** For each iface task, the gold symbols cgis's own impact answer lacks are
   listed, so that a recall loss in the forced arm can be traced to the graph or to the agent.

## Analysis

`benchmarks/agent_ab/analysis/exp4_gates.py` is committed verbatim before the main run. The pick and
gold tools are committed under `benchmarks/agent_ab/analysis/exp4/` before the pilot.

## Choice log (filled in by the walk; nothing above changes)

The searches ran at 2026-10-16T14:09:57Z, after the pre-registration commit 1a10032
(14:09:31Z).
- **Pool.** 300 repositories. The only excluded repository among them was uber-go/zap, which leaves
  299. Seeded order: `OJ/gobuster`, `lima-vm/lima`, `cilium/cilium`, and so on.
- **Walk.** Two candidates:
  1. `OJ/gobuster` failed the size check: 9,678 lines.
  2. **`lima-vm/lima` passed every check and is chosen:**
     - sha `f47461ed7d03f300297e01fd659e9aefc6c228b9`, module `github.com/lima-vm/lima/v2`;
     - 50,577 non-test lines, of which 5.3% are generated;
     - 1 of 243 non-test files has type parameters;
     - 20 interfaces, 13 of them with two or more in-module implementations.
- **Artifacts.** The search outputs, the pool, the walk script, its log and the interface counter
  (`ifc.go`) are in `benchmarks/agent_ab/analysis/exp4/selection/`.

**Recorded on choice, before any gold or cgis run on lima:**
- **Nested modules.** `website/` and `hack/tools/` are deleted from the worktree, as above.
- **Agent instruction files.** The repository carries `CLAUDE.md` and `AGENTS.md`, which Claude Code
  reads. They are kept, identical in both arms, because a real repository has them.
- **Amendment to task eligibility.** lima is split by platform: 56 non-test files carry `//go:build`
  constraints, mostly darwin against linux. The cgis graph is built for linux/amd64 (#667), and gold
  as specified does not name a platform. A target whose callers differ between the linux and darwin
  builds would therefore score the arms against an arbitrary platform choice. A target is eligible
  only if its required caller closure is identical under `GOOS=linux` and `GOOS=darwin` (both
  `GOARCH=amd64`), and gold is the linux closure. Any target that fails this is dropped before the
  draw, and the count is reported.

## Amendment 2: function values end a chain (2026-10-16, before any gold is frozen)

Committed before the gold is rebuilt, before the pilot, and before any cgis command has run on lima.

**What happened under the rules above.** The draw ran once on lima, exactly as written. It produced
0 iface tasks and 3 static tasks. F2 (gold size) left 627 targets; F3 (no function-value reference
in the caller closure) left 46, none of them iface; the platform check left 9 static. The primary
gate cannot be evaluated on that.

**Why.** F3 came from kafka-go, a library. lima is a command-line application, and nearly every
chain in it ends in a framework that holds a function value: cobra's `RunE: startAction`, gRPC
handler tables, `http.HandlerFunc`. F3 therefore excluded every target in the most common kind of
Go program. That is a fault in the rule, not in the repository, so the repository stays.

**The amended rule.** It replaces the last sentence of *Function values* and the F3 eligibility
line:
- A function-value reference is not an edge, and the chain is not followed through it. The
  referenced function is an entry point, like a function nothing calls. In the closure walk this
  means `R` records are not traversed. Before the amendment the tooling traversed them, and F3
  made that moot.
- The function that takes the reference (`newStartCommand` for `RunE: startAction`) is
  **allowed**: naming it is not penalised, and it is not required. Its own callers are not
  allowed.
- A target is no longer excluded for a function-value reference in its closure.
- The question gains one clause, identical in both arms: a function that the code only passes as a
  value, such as a cobra `RunE` handler, counts as an entry point.

**Shortfall and surplus rules**, which the protocol lacked:
- **More than 12 iface picks.** A fresh `random.Random(4).sample` over the picks, in draw order,
  keeps 12.
- **8 to 11 iface picks.** The run goes ahead with all of them, and the primary gate is evaluated on
  that n.
- **Fewer than 8 iface picks.** The primary gate is not evaluable on lima. The seeded walk then
  continues to the next candidate under these amended rules, and lima's counts are reported.
- **Fewer than 6 static picks.** All are taken. The static cell is descriptive only.

**Readings the protocol left open, now fixed:**
- "Drawn with the same seed" means a fresh `random.Random(4)`, not the generator left over from the
  iface draw.
- "Distinct method names and receiver types" means both must differ from every earlier pick in the
  family.
- Package initialisers, test helpers, third-party interfaces and goroutine launchers are ruled as in
  `benchmarks/agent_ab/analysis/exp4/gold/README.md`.

**Harness.** `scripts/agent_ab.py` builds each session's worktree with `git worktree add`, which
restored `website/` and `hack/tools/` in both arms. A task now lists `delete_paths`. They are
deleted before ingest and before the agent starts, and a missing path fails the session.

## Choice log, continued under amendment 2

- **lima-vm/lima is not evaluable.** Under amendment 2 its draw gives 7 iface picks and 6 static
  ones (`benchmarks/agent_ab/analysis/exp4/gold/out/lima/picks.json`), which is below the floor of
  8. DriverClient's family gives 1 of 3, because all its members are methods of the one generated
  `driverClient` type.
- **The walk resumed at 18:27Z.** cilium, hysteria, ffuf, containerlab, pb, nsq, goka, muraena,
  fabric, x/mobile, x/tools and VictoriaMetrics failed. Each failure is logged in `walk-log.jsonl`.
- **`golang/crypto` passed every check and is chosen:**
  - sha `0ae6809b7559060fe62abe65e26bd18b1fcaf6b7`, module `golang.org/x/crypto`;
  - 38,246 non-test lines, none generated;
  - no type parameters in its 128 files;
  - 32 interfaces, 22 of them with two or more in-module implementations.
- **Gold and draw follow** under the amended rules. No cgis command has run on it.

## Amendment 3: test helpers in a library (2026-10-16, before any gold is frozen)

Committed after the golang/crypto task draw was seen, before the crypto gold is frozen, before the
pilot, and before any cgis command has run on crypto.

**What happened.** The test-helper rule (gold README: "imported by at least one `_test.go` file and
by no non-test package") was written while working on lima, an application. In a library every
public leaf package looks like that. On golang/crypto it marked 12 packages, and 10 of them are
production API: `acme/autocert`, `ed25519`, `hkdf`, `md4`, `nacl/auth`, `nacl/box`, `scrypt`,
`sha3`, `ssh/agent` and `ssh/terminal`. Their functions could not be targets and were never
required callers. For example, `agent.Key.Verify` calls `rsaPublicKey.Verify` but was only
allowed. That gold would misstate "production code only".

**The amended rule.** A non-test package is a test helper when both hold:
- no non-test package imports it, under any of GOOS linux, darwin and windows; and
- its import path, relative to the module, has a segment that is `internal` or contains `test`.

**Disclosure.** This rule changes after the draw was seen, which the protocol is meant to prevent.
What was seen is which targets were drawn. No cgis answer and no session was seen, so the change
cannot favour either arm. The reason is a misclassification that is visible without the draw. The
draw is rerun with the same seed, and the first draw's picks are kept in the record.

## Before the pilot

- **#661 collisions on the crypto graph: 0.** The graph is a full `cgis ingest` of the working
  copy, with nested modules deleted. Of 20,754 CALLS edges, 1,609 are unresolved (confidence 0.8).
  1,605 of those point to UNKNOWN placeholders and 4 to STDLIB ones. None has a target equal to an
  INTERNAL function or method id.
- **`benchmarks/agent_ab/analysis/exp4_gates.py`** is committed with this entry, before any session.

## Scope of the conclusion (recorded during the pilot, before any pilot result was read)

- **Concentration.** 8 of the 12 iface tasks come from `ssh` (7 in `ssh`, 1 in `ssh/agent`), 3 from
  `pkcs12` and 1 from `acme/autocert`. The gate therefore measures mostly one subsystem of one
  library. lima, an application, was not evaluable.
- **Familiarity.** golang/crypto is widely known, and the model may know its structure from
  training. That helps the control arm, which makes a pass conservative and a fail less clean: a
  fail could partly reflect the model's prior knowledge rather than the graph.
- **How the result will be worded, whichever way it goes.** The README and MCP text name the scope:
  "measured on one repository, golang/crypto (mostly its ssh package), a library; not measured on
  applications." A pass adds Go to the impact-question languages with that qualifier. Replication
  on an application is a separate experiment.
- The gate, the draw and the tasks are unchanged.

## Pilot and gold freeze (2026-10-16)

- **Pilot.** 36 sessions, one per task per arm. Cost $2.24. Every transcript shows
  `apiKeySource: ANTHROPIC_API_KEY`, none failed, and every answer parsed. The results are in
  `benchmarks/agent_ab/results-exp4-pilot.jsonl`. They are not scored.
- **Adjudication.** Every symbol a pilot named that gold lacked, and every gold symbol both pilots
  missed, was read against the code.
  - **Gold was wrong in 2 tasks, the same way.** A function value the code calls at once in the
    function that takes it was treated as a reference (amendment 2) and not walked:
    - `Session.start` calls `(*Session).stdin/stdout/stderr` from a local slice
      (ssh/session.go:376-378);
    - `ListenTCP`, `ListenUnix` and `listenTCPInternal` call
      `c.handleForwardsOnce.Do(c.handleForwards)`, and `sync.Once.Do` calls its argument before
      it returns.

    These 6 sites are walked as calls (`gold/pilot-corrections-crypto.json`). As a result,
    `channel-channeltype` gains 5 required symbols (through `Client.Listen`) and `channel-stderr`
    gains 6 (through `Session.Start`, `Run`, `Output`, `Shell` and `CombinedOutput`).
  - **Allowed helpers.** Gold's walk stopped at the first test helper, so `acmeprobe`'s callers of
    that helper were unexpected. Every helper on a chain is now allowed, as the pre-registration's
    "Allowed: test helpers" says. This affects `static-tlschallengecert`.
  - **Not gold errors:**
    - `tlsChallengeCert`'s `Manager.cert`, `startRenew` and `domainRenewal.start` were missed by
      both arms, but they are right by the launcher rule (`time.AfterFunc(dr.renew)`).
    - The `mux.loop` and auth chain named for `cbcCipher.readCipherPacket` does not reach it,
      because `handshakeTransport.readPacket` only reads a Go channel (ssh/handshake.go:228).
    - Interface-method names such as `ssh.Channel.Stderr` are declarations, not callers.
    - The references held by `Manager.TLSConfig` (a tls hook) and `knownhosts.New` (a callback)
      correctly end their chains.
- **The draw is unchanged.** Eligibility is not recomputed after the pilot.
- **Gold is frozen** at the commit that adds this entry.
- **Reading caution, recorded before the main run.** In the pilot, most sessions cost
  $0.04–0.17, and the control arm usually reached full recall with a few greps. The recall
  condition is therefore likely to hold, and the gate will turn on the cost ratio of short
  sessions. Fixed costs (system prompt, tool descriptions, the forced first cgis call) weigh
  heavily in those. A fail means "no saving on easy questions in this library", not "the graph is
  useless for Go". A pass carries the scope already recorded. The gate is unchanged.

## Result (2026-10-16)

Main run: 108 sessions at 2f1d166 with gold frozen. All transcripts show
`apiKeySource: ANTHROPIC_API_KEY`. None failed or was contaminated, and every cgis-forced session
called cgis. Cost $6.32. Results: `benchmarks/agent_ab/results-exp4.jsonl`. Gates:
`exp4_gates.py`.

**Primary (iface, n = 12):**
- median cost ratio **0.810** (bootstrap 95% CI 0.67–0.95), against a threshold of ≤ 0.80;
- median Δrecall **+0.000** (CI +0.00–+0.00).

The point estimate decides: **the Go pack does not help**. Per the decision fixed above, the README
and MCP instructions say Go is supported for structure and metrics only, and the coverage line on a
Go graph tells the agent to confirm callers by reading the code. The scope recorded before the
pilot applies: one library, mostly `ssh`, with easy questions.

**Secondary (descriptive):**
- static (n = 6): ratio 0.747, Δrecall +0.000;
- all 18 tasks: ratio 0.796, Δrecall +0.000;
- median precision on iface: control 1.00, cgis-forced 0.90;
- forced sessions take fewer turns and less wall time on most tasks, and 1 of 54 forced sessions
  read a whole file.

Two tasks lost recall in the forced arm: `channel-channeltype` (−0.46) and `dircache-delete`
(−0.21). `encryptedcontentinfo-data` (+0.33) and `static-curvepoint-put` (+0.30) gained.

**Known-miss check** (`exp4/known_misses.py`): recall of cgis's own `impact` answer against gold,
per iface task:
- 1.00 on 5 tasks, 0.94–0.96 on 2;
- 0.75 on `dircache-delete`, 0.50 on `channel-channeltype`, 0.35 on `channel-stderr`,
  0.20 on `keyboardinteractivechallenge-auth` and 0.08 on `cbccipher-readcipherpacket`.

The forced arm recovered the last three by grepping, which cost money. It did not recover the
first two, and that is where its recall fell. The graph gaps behind this:
1. **Function values called synchronously or by a launcher have no edge.** This covers
   `sync.Once.Do(c.handleForwards)`, `time.AfterFunc(d, dr.renew)` and a local slice of method
   expressions. It causes the misses in `channeltype`, `dircache` and `stderr`, and both recall
   losses.
2. **Dispatch on `packetConn.readPacket` has no targets.** Nothing in the graph reaches
   `transport.readPacket`, so `cbcCipher.readCipherPacket` has almost no callers.
3. **The call `auth.auth(...)` stays unresolved** when `auth` is the loop variable of
   `for auth := AuthMethod(new(noneAuth)); auth != nil; {` (ssh/client_auth.go:79).
