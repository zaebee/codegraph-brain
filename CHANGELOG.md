# Changelog

## [0.14.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.13.0...codegraph-brain-v0.14.0) (2026-08-16)


### Features

* **guardian:** give measured recall a reviewer it can name ([#390](https://github.com/zaebee/codegraph-brain/issues/390)) ([#391](https://github.com/zaebee/codegraph-brain/issues/391)) ([acc1887](https://github.com/zaebee/codegraph-brain/commit/acc18874b7ce67fa38b7dab41552838f9578516d))
* **guardian:** say whether a temperature was chosen or inherited ([#393](https://github.com/zaebee/codegraph-brain/issues/393)) ([#395](https://github.com/zaebee/codegraph-brain/issues/395)) ([1b2c3c3](https://github.com/zaebee/codegraph-brain/commit/1b2c3c3f1619f00d20c36fb315eaca3dce5661d9))


### Bug Fixes

* **guardian:** close the deferred cleanups that could only narrow silently ([#385](https://github.com/zaebee/codegraph-brain/issues/385)) ([#386](https://github.com/zaebee/codegraph-brain/issues/386)) ([e7020ff](https://github.com/zaebee/codegraph-brain/commit/e7020ff237964a66728f0b437f13b70dc8bfe2f9))
* **guardian:** refuse a model name carrying whitespace ([#382](https://github.com/zaebee/codegraph-brain/issues/382)) ([#389](https://github.com/zaebee/codegraph-brain/issues/389)) ([45fb110](https://github.com/zaebee/codegraph-brain/commit/45fb110d3e440e858f57c28cd3e343e61218697c))


### Documentation

* **bench:** a failed parse is not a draw ([#394](https://github.com/zaebee/codegraph-brain/issues/394)) ([a70b09b](https://github.com/zaebee/codegraph-brain/commit/a70b09bf1faf67ddefff2d7fdcf555aba51ced9f))
* **bench:** repeated rows are samples, not corrections ([#392](https://github.com/zaebee/codegraph-brain/issues/392)) ([e1fcc3f](https://github.com/zaebee/codegraph-brain/commit/e1fcc3f4728e61363d06b022b14bc66f45f3a031))

## [0.13.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.12.0...codegraph-brain-v0.13.0) (2026-08-15)


### Features

* **guardian:** an identity for a reviewer, that a commit does not fragment ([#375](https://github.com/zaebee/codegraph-brain/issues/375)) ([#383](https://github.com/zaebee/codegraph-brain/issues/383)) ([e339a38](https://github.com/zaebee/codegraph-brain/commit/e339a38743ddfdaac1e7c8f0724281844d5a8b38))

## [0.12.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.11.0...codegraph-brain-v0.12.0) (2026-08-13)


### Features

* **guardian:** an ablation arm, because G5 cannot separate graph from language ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#366](https://github.com/zaebee/codegraph-brain/issues/366)) ([e2374a7](https://github.com/zaebee/codegraph-brain/commit/e2374a72016413414241e71d3cc37a7ce251c590))
* **guardian:** bound the local finder's output, because it does not stop ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#381](https://github.com/zaebee/codegraph-brain/issues/381)) ([1e0b5e1](https://github.com/zaebee/codegraph-brain/commit/1e0b5e13332c697102a22838f90fed265c0fec67))
* **guardian:** expose judge concurrency, because Mistral needs it at 1 ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#364](https://github.com/zaebee/codegraph-brain/issues/364)) ([47392e8](https://github.com/zaebee/codegraph-brain/commit/47392e829e8653e6d145533a637342e8a46b4982))
* **guardian:** keep the valid prefix of a truncated finder response ([#248](https://github.com/zaebee/codegraph-brain/issues/248)) ([#377](https://github.com/zaebee/codegraph-brain/issues/377)) ([d34f01f](https://github.com/zaebee/codegraph-brain/commit/d34f01fa11208c0f8202579edc7e07dccb163ad4))
* **guardian:** record what the judge spent, per row ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#378](https://github.com/zaebee/codegraph-brain/issues/378)) ([c4eea31](https://github.com/zaebee/codegraph-brain/commit/c4eea31b12492d90dafb92b3afacfbe9a942bab5))
* **guardian:** refuse a review of a truncated prompt, and supply the missing visitor ([#248](https://github.com/zaebee/codegraph-brain/issues/248)) ([#371](https://github.com/zaebee/codegraph-brain/issues/371)) ([5f3e9b7](https://github.com/zaebee/codegraph-brain/commit/5f3e9b73bcc36aabe64d634e4aa95ea0f5bd7fb7))
* **guardian:** review --slice, so the registered population is a command ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#373](https://github.com/zaebee/codegraph-brain/issues/373)) ([4d1fe6a](https://github.com/zaebee/codegraph-brain/commit/4d1fe6a8073641cdd46a9d9af24a3c31abd70764))
* **guardian:** sampling reaches Ollama, instead of the chat template deciding ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#380](https://github.com/zaebee/codegraph-brain/issues/380)) ([14cb966](https://github.com/zaebee/codegraph-brain/commit/14cb9664f200aa9507f1f364f72dfb0de0981259))
* **guardian:** send the registered temperature, and survive a 429 ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#372](https://github.com/zaebee/codegraph-brain/issues/372)) ([3d7efd8](https://github.com/zaebee/codegraph-brain/commit/3d7efd82efe750283a43eb0d5cb7941f7f67ddd1))
* **guardian:** the union arm scores offline, and a unit bug made G8 unfailable ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#370](https://github.com/zaebee/codegraph-brain/issues/370)) ([dd290fe](https://github.com/zaebee/codegraph-brain/commit/dd290fef986ad0dcf0b62da9fa2824e0ace1b90d))


### Bug Fixes

* **guardian:** a dead judge is not a reviewer that found nothing ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#376](https://github.com/zaebee/codegraph-brain/issues/376)) ([2772c30](https://github.com/zaebee/codegraph-brain/commit/2772c305e9d7ca70c31bdfc2b12297392768330c))
* **guardian:** a truncated review is not a review that found nothing ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#374](https://github.com/zaebee/codegraph-brain/issues/374)) ([9f86bb6](https://github.com/zaebee/codegraph-brain/commit/9f86bb63e297e2b8bdda6bf6214e5d1e1bbd9339))
* **guardian:** the ablation must skip PRs with no graph to remove ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#367](https://github.com/zaebee/codegraph-brain/issues/367)) ([f9c36f5](https://github.com/zaebee/codegraph-brain/commit/f9c36f5cae503d83002ad03957eb7bf0cf31d263))


### Documentation

* **guardian:** bench a local model in a notebook, and retire a wrong lever ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#379](https://github.com/zaebee/codegraph-brain/issues/379)) ([419fcc1](https://github.com/zaebee/codegraph-brain/commit/419fcc1eb28fddd9a2c0cef814480d861e6d8c88))
* **spec:** Phase 2 results — G5 fails at +9.5 pp against a 10 pp gate ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#362](https://github.com/zaebee/codegraph-brain/issues/362)) ([9481a54](https://github.com/zaebee/codegraph-brain/commit/9481a54c02b60786e14e6317d3a71c8a4cee12a0))
* **spec:** Phase 3 registers the union arm, and the pilot says it is marginal ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#369](https://github.com/zaebee/codegraph-brain/issues/369)) ([bc9caf4](https://github.com/zaebee/codegraph-brain/commit/bc9caf43c16a2ce27a0075b14eb0766569ae8c2d))
* **spec:** R5 — the noise floor equals the effect, so Phase 2 cannot answer its question ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#368](https://github.com/zaebee/codegraph-brain/issues/368)) ([8540bf1](https://github.com/zaebee/codegraph-brain/commit/8540bf1178a8535849bdd2fadfa5473f42040eb8))
* **spec:** second judge — G5 fails under both, and the row becomes publishable ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#365](https://github.com/zaebee/codegraph-brain/issues/365)) ([62c49e4](https://github.com/zaebee/codegraph-brain/commit/62c49e4b36c0950df0c10cce26c09418674130eb))

## [0.11.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.10.0...codegraph-brain-v0.11.0) (2026-08-12)


### Features

* **collector:** collect TypeScript context via a language registry ([#344](https://github.com/zaebee/codegraph-brain/issues/344)) ([#349](https://github.com/zaebee/codegraph-brain/issues/349)) ([f40f22f](https://github.com/zaebee/codegraph-brain/commit/f40f22fcfae57602fff24ae183bfc8977f246473)), closes [#342](https://github.com/zaebee/codegraph-brain/issues/342)
* **guardian:** Martian corpus layer, and the profiles are not what the spec said ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#355](https://github.com/zaebee/codegraph-brain/issues/355)) ([16f9640](https://github.com/zaebee/codegraph-brain/commit/16f96408e46bfe9d5544fa10dd4ea9d68b2e8f34))
* **guardian:** Phase 1 calibration harness — score recorded reviews with Martian's judge ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#346](https://github.com/zaebee/codegraph-brain/issues/346)) ([6d7771a](https://github.com/zaebee/codegraph-brain/commit/6d7771aad9b7bb1bfc7b26a0ed2c9d56b2715466))
* **guardian:** Phase 2 judge pass, and the first scored PR ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#359](https://github.com/zaebee/codegraph-brain/issues/359)) ([eaf6626](https://github.com/zaebee/codegraph-brain/commit/eaf6626ea473e0c0bd8f2468f4fc989d29a3decf))
* **guardian:** Phase 2 planner — resolve slices before spending anything ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#356](https://github.com/zaebee/codegraph-brain/issues/356)) ([75a278d](https://github.com/zaebee/codegraph-brain/commit/75a278d5caad07e3865c1fa95ed79c5f63e64640))
* **guardian:** Phase 2 report — G4/G5/G6, and G5 refuses a vacuous comparison ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#360](https://github.com/zaebee/codegraph-brain/issues/360)) ([14d0fc4](https://github.com/zaebee/codegraph-brain/commit/14d0fc4fb989c1ae0224e3367de30f1b202a701d))
* **guardian:** Phase 2 review step, and the first paid run ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#358](https://github.com/zaebee/codegraph-brain/issues/358)) ([933c229](https://github.com/zaebee/codegraph-brain/commit/933c2298b1051962c6860a99ea584b8ca6f204e5))
* **guardian:** Phase 2 workspace — prepare finds the bug that would have sunk G5 ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#357](https://github.com/zaebee/codegraph-brain/issues/357)) ([1ecd962](https://github.com/zaebee/codegraph-brain/commit/1ecd9629f46cab10b907dae285d0f58b0eef5e21))


### Bug Fixes

* **guardian:** an ambiguous hit is a false positive ([#345](https://github.com/zaebee/codegraph-brain/issues/345)) ([#348](https://github.com/zaebee/codegraph-brain/issues/348)) ([fa37624](https://github.com/zaebee/codegraph-brain/commit/fa37624721a3a58a8b33cb01af71b7a7cb0bd34c)), closes [#342](https://github.com/zaebee/codegraph-brain/issues/342)
* **guardian:** give record_review a contract for the path it writes to ([#347](https://github.com/zaebee/codegraph-brain/issues/347)) ([#350](https://github.com/zaebee/codegraph-brain/issues/350)) ([af968b9](https://github.com/zaebee/codegraph-brain/commit/af968b90d931c8152b5c9372d343cec44c8281fe))


### Documentation

* **spec:** auto-evolution PoC — unit of selection, fitness, first mutation gate ([#335](https://github.com/zaebee/codegraph-brain/issues/335)) ([#336](https://github.com/zaebee/codegraph-brain/issues/336)) ([b7359a7](https://github.com/zaebee/codegraph-brain/commit/b7359a78b3734ff5f23131b28b6923de6bc37707))
* **spec:** first live data falsifies two parts of the PoC gate ([#335](https://github.com/zaebee/codegraph-brain/issues/335)) ([#338](https://github.com/zaebee/codegraph-brain/issues/338)) ([a417e89](https://github.com/zaebee/codegraph-brain/commit/a417e8977505abd49ddf7c2642a7eeb5614d578a))
* **spec:** Guardian vs Martian Code Review Bench — Phase 1 calibration results ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([7ca8434](https://github.com/zaebee/codegraph-brain/commit/7ca8434ff1363ef6c8c8d0cf97541e7e2039e098))
* **spec:** Phase 2 corpus reconnaissance and the amended G5 ([#342](https://github.com/zaebee/codegraph-brain/issues/342)) ([#354](https://github.com/zaebee/codegraph-brain/issues/354)) ([e63ec24](https://github.com/zaebee/codegraph-brain/commit/e63ec244f61988048a915e9c7564e897ab68d6cf))

## [0.10.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.9.0...codegraph-brain-v0.10.0) (2026-08-01)


### Features

* **guardian:** kinship-grouped axis batches behind GUARDIAN_FEATURES=axes_paired ([#334](https://github.com/zaebee/codegraph-brain/issues/334)) ([7951dcb](https://github.com/zaebee/codegraph-brain/commit/7951dcb60a2fff2f232b144af76043a9d06d3eee))
* **guardian:** per-axis review fan-out behind GUARDIAN_FEATURES=axes ([#333](https://github.com/zaebee/codegraph-brain/issues/333)) ([ada3205](https://github.com/zaebee/codegraph-brain/commit/ada3205e54433356f3a1e9ef4944ed5475ed2d7c)), closes [#331](https://github.com/zaebee/codegraph-brain/issues/331)


### Documentation

* **spec:** record the Arm A result — fails its gate, and shows the mechanism ([#330](https://github.com/zaebee/codegraph-brain/issues/330)) ([af8d543](https://github.com/zaebee/codegraph-brain/commit/af8d543fb656af233a961ef081bc8fed5f4331a0))

## [0.9.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.8.0...codegraph-brain-v0.9.0) (2026-08-01)


### Features

* **drift:** look through transparent re-exports in the IMPORTS census ([#329](https://github.com/zaebee/codegraph-brain/issues/329)) ([25b2ce6](https://github.com/zaebee/codegraph-brain/commit/25b2ce6049ae878ebeef05cd47b08dd67faa763d))
* **extractor:** detect transparent re-exports ([#182](https://github.com/zaebee/codegraph-brain/issues/182) direction 1, slice 1) ([#327](https://github.com/zaebee/codegraph-brain/issues/327)) ([6e6e654](https://github.com/zaebee/codegraph-brain/commit/6e6e654b4ce17c52487507d2d425a4f81045f731))

## [0.8.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.5...codegraph-brain-v0.8.0) (2026-08-01)


### Features

* **ontology:** bind guardian, cli and api as measured domains ([#325](https://github.com/zaebee/codegraph-brain/issues/325)) ([e7678e8](https://github.com/zaebee/codegraph-brain/commit/e7678e899bf09e5567ea1fa19180835984b652c9))

## [0.7.5](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.4...codegraph-brain-v0.7.5) (2026-08-01)


### Documentation

* **spec:** record what changed for [#258](https://github.com/zaebee/codegraph-brain/issues/258) — fixture, second lever, pre-registered gate ([#322](https://github.com/zaebee/codegraph-brain/issues/322)) ([fca8da8](https://github.com/zaebee/codegraph-brain/commit/fca8da8120a7542a71c875d9a960df615f59594e))

## [0.7.4](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.3...codegraph-brain-v0.7.4) (2026-08-01)


### Bug Fixes

* **mcp:** validate db_path before cgis_ingest creates a database ([#313](https://github.com/zaebee/codegraph-brain/issues/313)) ([2c5499b](https://github.com/zaebee/codegraph-brain/commit/2c5499b8fb7f0d40a03144417c3210900371d499)), closes [#312](https://github.com/zaebee/codegraph-brain/issues/312)


### Documentation

* auto-sync architecture graph and MCP reference ([#314](https://github.com/zaebee/codegraph-brain/issues/314)) ([7d5b666](https://github.com/zaebee/codegraph-brain/commit/7d5b66669f9dfa40e2fc86dd59a4baf492a9342e))

## [0.7.3](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.2...codegraph-brain-v0.7.3) (2026-07-31)


### Bug Fixes

* **assets:** drop the argv-driven output path from the avatar generator ([#308](https://github.com/zaebee/codegraph-brain/issues/308)) ([d118074](https://github.com/zaebee/codegraph-brain/commit/d118074a6b2137f789b4978f870f5496ee1135b5))
* **ci:** read the App ID from a secret as well as a variable ([#306](https://github.com/zaebee/codegraph-brain/issues/306)) ([90513bc](https://github.com/zaebee/codegraph-brain/commit/90513bc3f588241848fd7404f9be5b6479a72293))

## [0.7.2](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.1...codegraph-brain-v0.7.2) (2026-07-31)


### Bug Fixes

* **ci:** install guardian deps with --extra, not --group ([#300](https://github.com/zaebee/codegraph-brain/issues/300)) ([1da003d](https://github.com/zaebee/codegraph-brain/commit/1da003d811db8e60201693905a6f83c2fccf48cf))
* **cli:** escape data interpolated into Rich console output ([#298](https://github.com/zaebee/codegraph-brain/issues/298)) ([dd4943d](https://github.com/zaebee/codegraph-brain/commit/dd4943d0fea1100b47130fcb0790e940da83276a))
* **drift:** pin measured depth when a domain is shallower than its gate ([#297](https://github.com/zaebee/codegraph-brain/issues/297)) ([1ebb93a](https://github.com/zaebee/codegraph-brain/commit/1ebb93a888037af5ad333ddffc7896af61f6eb5e)), closes [#229](https://github.com/zaebee/codegraph-brain/issues/229)


### Documentation

* add the chunked-review lab note (a negative result worth keeping) ([#299](https://github.com/zaebee/codegraph-brain/issues/299)) ([265fefd](https://github.com/zaebee/codegraph-brain/commit/265fefda7ee30ece692d13ea0bb171d99ac18bd9)), closes [#160](https://github.com/zaebee/codegraph-brain/issues/160)

## [0.7.1](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.7.0...codegraph-brain-v0.7.1) (2026-07-31)


### Documentation

* add privacy policy ([#293](https://github.com/zaebee/codegraph-brain/issues/293)) ([33e45c2](https://github.com/zaebee/codegraph-brain/commit/33e45c2623d6d4a4c05106631fe2815ce1acf62e))
* add the architecture audit offering ([#296](https://github.com/zaebee/codegraph-brain/issues/296)) ([086396d](https://github.com/zaebee/codegraph-brain/commit/086396dbfa569a5091439811d6ec00e1b094386f))

## [0.7.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.6.0...codegraph-brain-v0.7.0) (2026-07-31)


### Features

* **plugin:** ship CGIS as a Claude Code plugin ([#290](https://github.com/zaebee/codegraph-brain/issues/290)) ([c93c7ed](https://github.com/zaebee/codegraph-brain/commit/c93c7ed377943081525c8965bc3a7ce5e5ccf7c1))


### Documentation

* **plugin:** disclose the PyPI fetch and the local-only guarantee ([#291](https://github.com/zaebee/codegraph-brain/issues/291)) ([278560c](https://github.com/zaebee/codegraph-brain/commit/278560c74d7d9deae076511ce8659c233c41bcfc))
* **specs:** finder bug-class taxonomy + a real Resource Management fixture ([#258](https://github.com/zaebee/codegraph-brain/issues/258)) ([#288](https://github.com/zaebee/codegraph-brain/issues/288)) ([1e02d06](https://github.com/zaebee/codegraph-brain/commit/1e02d06a1599d87dbc4958cb6e6db6e0acaae605))

## [0.6.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.5.0...codegraph-brain-v0.6.0) (2026-07-31)


### Features

* **guardian:** chunk only reviewable source ([#277](https://github.com/zaebee/codegraph-brain/issues/277)) ([#281](https://github.com/zaebee/codegraph-brain/issues/281)) ([4b1a187](https://github.com/zaebee/codegraph-brain/commit/4b1a1873a870c3f67d5e627214d1116335eebcbf))


### Bug Fixes

* **guardian:** close both Gemini connection pools ([#283](https://github.com/zaebee/codegraph-brain/issues/283)) ([#284](https://github.com/zaebee/codegraph-brain/issues/284)) ([b2ae97b](https://github.com/zaebee/codegraph-brain/commit/b2ae97bfb0d7c73754aa4cb1fa04b22c98811abb))


### Documentation

* case study — CGIS on a 12-repository estate ([#286](https://github.com/zaebee/codegraph-brain/issues/286)) ([cdfe6a3](https://github.com/zaebee/codegraph-brain/commit/cdfe6a3c8751768b03d3b31e8f1d66f3c770070e))

## [0.5.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.4.0...codegraph-brain-v0.5.0) (2026-07-30)


### Features

* **fractal:** cgis_fractal — structural tier ladder + entropy slope ([#186](https://github.com/zaebee/codegraph-brain/issues/186)) ([#274](https://github.com/zaebee/codegraph-brain/issues/274)) ([2e768ce](https://github.com/zaebee/codegraph-brain/commit/2e768cef585f10481970697657b0a9b40a411f47))


### Bug Fixes

* **guardian:** explicit request timeout + bounded retry ([#275](https://github.com/zaebee/codegraph-brain/issues/275)) ([#278](https://github.com/zaebee/codegraph-brain/issues/278)) ([e2ee313](https://github.com/zaebee/codegraph-brain/commit/e2ee31328dc0e19f362d03431e52d4c33404d188))

## [0.4.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.3.0...codegraph-brain-v0.4.0) (2026-07-29)


### Features

* **bench:** report which ground truth the skeptic killed ([#270](https://github.com/zaebee/codegraph-brain/issues/270)) ([#272](https://github.com/zaebee/codegraph-brain/issues/272)) ([a830d92](https://github.com/zaebee/codegraph-brain/commit/a830d92c411d1fccdca26dfab1d3267edc4c103c))
* **guardian:** per-finding skeptic judgement + impact scoring ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#269](https://github.com/zaebee/codegraph-brain/issues/269)) ([038a501](https://github.com/zaebee/codegraph-brain/commit/038a5013361dfb2465e442760ded1cb26c7d4183))
* **mcp:** migrate to mcp 2.x (MCPServer), lift the &lt;2 bound ([#273](https://github.com/zaebee/codegraph-brain/issues/273)) ([e0d76da](https://github.com/zaebee/codegraph-brain/commit/e0d76dada2b3f96c816021a729616c5a7d3927b3)), closes [#264](https://github.com/zaebee/codegraph-brain/issues/264)


### Bug Fixes

* **context:** resolve source_root against stored paths, first match wins ([#228](https://github.com/zaebee/codegraph-brain/issues/228)) ([#263](https://github.com/zaebee/codegraph-brain/issues/263)) ([ecf87de](https://github.com/zaebee/codegraph-brain/commit/ecf87deb5a683317f49cfdb47e23764e8dfb9464))


### Documentation

* auto-sync architecture graph and MCP reference ([#267](https://github.com/zaebee/codegraph-brain/issues/267)) ([4a68804](https://github.com/zaebee/codegraph-brain/commit/4a688049ac77c2edb1f86e8d28df926eb545c5be))
* **specs:** guardian skeptic — per-finding judgement + impact scoring ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#268](https://github.com/zaebee/codegraph-brain/issues/268)) ([b47bcbe](https://github.com/zaebee/codegraph-brain/commit/b47bcbee2dab06fd7b0aa8ac2c9f367e9746ea58))

## [0.3.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.2.0...codegraph-brain-v0.3.0) (2026-06-14)


### Features

* **audit:** reachability/authz coverage primitive — `cgis audit` ([#172](https://github.com/zaebee/codegraph-brain/issues/172)) ([#236](https://github.com/zaebee/codegraph-brain/issues/236)) ([7ec4a28](https://github.com/zaebee/codegraph-brain/commit/7ec4a289ecd5acd962603b8bf1c2e256018b5b38))
* cgis_find_symbol — partial name → ranked FQNs ([#173](https://github.com/zaebee/codegraph-brain/issues/173)) ([#206](https://github.com/zaebee/codegraph-brain/issues/206)) ([eaeb5a6](https://github.com/zaebee/codegraph-brain/commit/eaeb5a6e3df86e3a82120bde60ec771fb3747d6e))
* **drift:** fit-quality reporting + funnel template ([#177](https://github.com/zaebee/codegraph-brain/issues/177), [#186](https://github.com/zaebee/codegraph-brain/issues/186)) ([#232](https://github.com/zaebee/codegraph-brain/issues/232)) ([82aaf15](https://github.com/zaebee/codegraph-brain/commit/82aaf15a8d55da6df6f39cf4d1aced5871398d52))
* **drift:** gate semantics v2 — intra-domain cycles, gate_failed, tolerance precedence ([#176](https://github.com/zaebee/codegraph-brain/issues/176), [#170](https://github.com/zaebee/codegraph-brain/issues/170)) ([#221](https://github.com/zaebee/codegraph-brain/issues/221)) ([303a502](https://github.com/zaebee/codegraph-brain/commit/303a502afd26ad72d9316b7a17dcb25e39413d24))
* **drift:** tangle_ratio hygiene gate — the antisymmetry half of health ([#186](https://github.com/zaebee/codegraph-brain/issues/186)) ([#241](https://github.com/zaebee/codegraph-brain/issues/241)) ([96dc2e0](https://github.com/zaebee/codegraph-brain/commit/96dc2e0fdc8cd581c38ecb97424ddfd97c2e604e))
* **guardian:** Ollama provider for local/colab inference — finder + skeptic ([#256](https://github.com/zaebee/codegraph-brain/issues/256)) ([5748248](https://github.com/zaebee/codegraph-brain/commit/574824868d0efcac23a082874f8bc28381266f6b))
* **guardian:** recall-lean universal finder + skeptic reconciliation ([#249](https://github.com/zaebee/codegraph-brain/issues/249)) ([7bb1166](https://github.com/zaebee/codegraph-brain/commit/7bb1166e156ab6c4e23b101fb89b892e00d27f2d))
* **mermaid:** human/AI-readable node ids instead of MD5 hashes ([#210](https://github.com/zaebee/codegraph-brain/issues/210)) ([#213](https://github.com/zaebee/codegraph-brain/issues/213)) ([0b5ae7e](https://github.com/zaebee/codegraph-brain/commit/0b5ae7e86233bcf9d195c8c2a0ef921f96ef36f7))
* **metrics:** --exclude &lt;segment&gt; to drop test/vendor code from rankings ([#234](https://github.com/zaebee/codegraph-brain/issues/234)) ([#235](https://github.com/zaebee/codegraph-brain/issues/235)) ([19383d0](https://github.com/zaebee/codegraph-brain/commit/19383d0dd0819c2448ff60189703651fe83823c7))
* **metrics:** DuckDB analytical layer — `cgis metrics` ([#16](https://github.com/zaebee/codegraph-brain/issues/16), slice 1) ([#230](https://github.com/zaebee/codegraph-brain/issues/230)) ([7bd74f9](https://github.com/zaebee/codegraph-brain/commit/7bd74f9aa3b2d7b08a5000ed24eae520c6a7a3e4))
* **metrics:** surface in/out degree on PageRank rows ([#237](https://github.com/zaebee/codegraph-brain/issues/237)) ([#238](https://github.com/zaebee/codegraph-brain/issues/238)) ([a072438](https://github.com/zaebee/codegraph-brain/commit/a07243871e4bcce6433c3cf66f972c0b16b14b17))
* **metrics:** vectorized PageRank — critical-nodes ranking ([#231](https://github.com/zaebee/codegraph-brain/issues/231)) ([#233](https://github.com/zaebee/codegraph-brain/issues/233)) ([4ecc011](https://github.com/zaebee/codegraph-brain/commit/4ecc01197eb1863189bbe9bb25fa2693bf7d9603))
* **query:** cgis init-ontology — auto-proposed patterns.yaml ([#174](https://github.com/zaebee/codegraph-brain/issues/174)) ([#211](https://github.com/zaebee/codegraph-brain/issues/211)) ([e73e3b1](https://github.com/zaebee/codegraph-brain/commit/e73e3b1ca82c94f4d987dd005842209b6f9ea0b7))
* **query:** cgis suggest-packages — graph-driven sub-package suggestions ([#242](https://github.com/zaebee/codegraph-brain/issues/242) slice 1) ([#245](https://github.com/zaebee/codegraph-brain/issues/245)) ([827bd04](https://github.com/zaebee/codegraph-brain/commit/827bd045f46cc25c72b961dab15e98da3ec9b3a2))
* **query:** GraphRAG prompt compiler — `cgis context <fqn>` ([#19](https://github.com/zaebee/codegraph-brain/issues/19)) ([#219](https://github.com/zaebee/codegraph-brain/issues/219)) ([f1a79e5](https://github.com/zaebee/codegraph-brain/commit/f1a79e5ca543cefcbb81dd8ec596967fadc4fe9d))
* **query:** JSON output for trace_flow / analyze_impact / structure ([#171](https://github.com/zaebee/codegraph-brain/issues/171)) ([#209](https://github.com/zaebee/codegraph-brain/issues/209)) ([39e4028](https://github.com/zaebee/codegraph-brain/commit/39e40282a33ab5aa6a52c08bfe06a8f629ffedf7))


### Bug Fixes

* **drift:** discount CALLS-layer tangle by unresolved_ratio ([#244](https://github.com/zaebee/codegraph-brain/issues/244)) ([#253](https://github.com/zaebee/codegraph-brain/issues/253)) ([119e5b3](https://github.com/zaebee/codegraph-brain/commit/119e5b3272871106841fbe0b847d9350ce091ff9))
* **guardian:** anchor inline comments to a verbatim quote, not the model's line ([#181](https://github.com/zaebee/codegraph-brain/issues/181)) ([#243](https://github.com/zaebee/codegraph-brain/issues/243)) ([ccb2503](https://github.com/zaebee/codegraph-brain/commit/ccb2503e7a127bbe1cfbde4196a6ce4db30c0c0a))
* **mcp:** cgis_ingest full_rebuild flag + whole-graph totals ([#192](https://github.com/zaebee/codegraph-brain/issues/192)) ([#223](https://github.com/zaebee/codegraph-brain/issues/223)) ([397836c](https://github.com/zaebee/codegraph-brain/commit/397836c33850f513c5f29d5cbede50de6c8622e9))
* **suggest:** connectivity guard — sparse package no longer false-'split' ([#257](https://github.com/zaebee/codegraph-brain/issues/257)) ([0e1ddf7](https://github.com/zaebee/codegraph-brain/commit/0e1ddf7743b3c8a542a1da3613073d43fcb79a51))
* **suggest:** single-module prefix no longer false-flags 'mis-rooted' ([#254](https://github.com/zaebee/codegraph-brain/issues/254)) ([b7efd13](https://github.com/zaebee/codegraph-brain/commit/b7efd139eab9537f384f88721255a1be1e9b24f9)), closes [#242](https://github.com/zaebee/codegraph-brain/issues/242)


### Documentation

* **architecture:** cgis self-portrait snapshot ([#252](https://github.com/zaebee/codegraph-brain/issues/252)) ([df611ce](https://github.com/zaebee/codegraph-brain/commit/df611cebe313432473b70d2f4a4897aec960811e))
* **architecture:** human/AI-readable reference for the 5 patterns + 13 triads ([#218](https://github.com/zaebee/codegraph-brain/issues/218)) ([1baf316](https://github.com/zaebee/codegraph-brain/commit/1baf316bb30c5ae0f5e2955681bd6a7679c0fa4e))
* auto-sync architecture graph and MCP reference ([#207](https://github.com/zaebee/codegraph-brain/issues/207)) ([0121536](https://github.com/zaebee/codegraph-brain/commit/01215368ae435585976dd39ecd463ecaff4f36c0))
* auto-sync architecture graph and MCP reference ([#212](https://github.com/zaebee/codegraph-brain/issues/212)) ([2023c66](https://github.com/zaebee/codegraph-brain/commit/2023c66af286e688fb733861b6103d698d10a929))
* auto-sync architecture graph and MCP reference ([#217](https://github.com/zaebee/codegraph-brain/issues/217)) ([9465478](https://github.com/zaebee/codegraph-brain/commit/9465478d5bfd51ce15e1601006cb7b2a014a39e6))
* auto-sync architecture graph and MCP reference ([#222](https://github.com/zaebee/codegraph-brain/issues/222)) ([a688995](https://github.com/zaebee/codegraph-brain/commit/a68899593223a1d62108f2c8aa80a521a3516502))
* auto-sync architecture graph and MCP reference ([#225](https://github.com/zaebee/codegraph-brain/issues/225)) ([deb94a0](https://github.com/zaebee/codegraph-brain/commit/deb94a013856df56170e1af59476fb1835b00921))
* auto-sync architecture graph and MCP reference ([#226](https://github.com/zaebee/codegraph-brain/issues/226)) ([adc6282](https://github.com/zaebee/codegraph-brain/commit/adc6282b1e738e958b8a4f3b38905016705487c5))
* auto-sync architecture graph and MCP reference ([#250](https://github.com/zaebee/codegraph-brain/issues/250)) ([56871db](https://github.com/zaebee/codegraph-brain/commit/56871db03f9f5fc81aff17bbca47a63f883cab48))
* **readme:** surface Guardian, drift gates, and local/cloud models ([#259](https://github.com/zaebee/codegraph-brain/issues/259)) ([0d8cc99](https://github.com/zaebee/codegraph-brain/commit/0d8cc994092394236fe18725339ce2e894611e4b))
* **specs:** drift gate semantics v2 — intra-domain cycles, gate_failed, tolerance precedence ([#176](https://github.com/zaebee/codegraph-brain/issues/176) + [#170](https://github.com/zaebee/codegraph-brain/issues/170)) ([#216](https://github.com/zaebee/codegraph-brain/issues/216)) ([ae120a1](https://github.com/zaebee/codegraph-brain/commit/ae120a1b49ac7259d12790d0d59be108ae786bdc))

## [0.2.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.1.0...codegraph-brain-v0.2.0) (2026-06-12)


### Features

* **#131:** add --source-root flag for FQN stripping ([#137](https://github.com/zaebee/codegraph-brain/issues/137)) ([42d5b55](https://github.com/zaebee/codegraph-brain/commit/42d5b553066b88740da42b20e74ae5e5dacc4975))
* **#139:** domain pattern fingerprint & drift engine ([#140](https://github.com/zaebee/codegraph-brain/issues/140)) ([74fe052](https://github.com/zaebee/codegraph-brain/commit/74fe052a70c29cce23aa8c64a4a8af34986df2b4))
* **#37:** Python OOP & decorator semantics ([#56](https://github.com/zaebee/codegraph-brain/issues/56)) ([61a7235](https://github.com/zaebee/codegraph-brain/commit/61a7235c51057fb47ae6cf3cacf4d79b78e8d423))
* **#47:** semantic uplift engine — L3 domain ontology mapping ([#64](https://github.com/zaebee/codegraph-brain/issues/64)) ([d118f32](https://github.com/zaebee/codegraph-brain/commit/d118f3228a5c4d1b04f9e3ea586160c861f79d9c))
* **#50:** self-referential architectural guardrails (L1/L2 linting) ([#61](https://github.com/zaebee/codegraph-brain/issues/61)) ([a8ca0d2](https://github.com/zaebee/codegraph-brain/commit/a8ca0d27a9ea0532de059c06438d76ee75cd74fe))
* **#58:** query filtering & noise pruning ([#59](https://github.com/zaebee/codegraph-brain/issues/59)) ([d8ff31f](https://github.com/zaebee/codegraph-brain/commit/d8ff31faa639989ea7663ae4cebf35fb5c6ac4c6))
* **#62:** self-documenting knowledge portal — scripts, CI, docs ([c735033](https://github.com/zaebee/codegraph-brain/commit/c7350338a25ad068821eb9af796679a3377b2313))
* **#77:** group mermaid nodes by file into subgraph blocks ([#82](https://github.com/zaebee/codegraph-brain/issues/82)) ([eb00683](https://github.com/zaebee/codegraph-brain/commit/eb0068359d6e324b1e1807530f5f7d6327d3b873))
* Added base extractor for python. ([9c1677a](https://github.com/zaebee/codegraph-brain/commit/9c1677a99b41efa2a9f62e4fd8c232fb49fd6844))
* Added base extractor for python. ([f685278](https://github.com/zaebee/codegraph-brain/commit/f685278c8c8ccc07bf67e1912ba1eba4de0d3dfd))
* Added core models with tests harness ([0bdd00d](https://github.com/zaebee/codegraph-brain/commit/0bdd00d58873531c3567182737e26a6fd0e36533))
* **analyzer:** architectural anti-pattern detection ([#69](https://github.com/zaebee/codegraph-brain/issues/69)) ([#101](https://github.com/zaebee/codegraph-brain/issues/101)) ([fd2440c](https://github.com/zaebee/codegraph-brain/commit/fd2440c5ada6713b115d459684b551a4ff6fd8d7))
* **classifier:** external/stdlib node classification via virtual nodes ([#49](https://github.com/zaebee/codegraph-brain/issues/49)) ([874cb3e](https://github.com/zaebee/codegraph-brain/commit/874cb3e7b6fb2e9e576eae594865f7ed832a5677))
* CLI trace/impact commands with rich tree visualization ([#10](https://github.com/zaebee/codegraph-brain/issues/10)) ([7c2e73e](https://github.com/zaebee/codegraph-brain/commit/7c2e73e5f51c3cb436caeb789dbdcc89927a924d))
* **cli:** add cgis validate command — graph integrity reporter ([#17](https://github.com/zaebee/codegraph-brain/issues/17)) ([#43](https://github.com/zaebee/codegraph-brain/issues/43)) ([b9b9f34](https://github.com/zaebee/codegraph-brain/commit/b9b9f342eccaf16a6657d9b92efad1579390888a))
* **diagram:** cleaner README graph — internal-only, self-doc note, source links ([#75](https://github.com/zaebee/codegraph-brain/issues/75)) ([d864986](https://github.com/zaebee/codegraph-brain/commit/d864986db8e3c14b5a1bccc884cc4c2eccbaa133))
* **drift:** loud zero-match domains — empty / no_signal statuses ([#178](https://github.com/zaebee/codegraph-brain/issues/178)) ([#196](https://github.com/zaebee/codegraph-brain/issues/196)) ([79dead9](https://github.com/zaebee/codegraph-brain/commit/79dead9ddd7169d984547596fe8f751e4ccd2325))
* **extractor+resolver:** Import Graph Linking — cross-file call resolution ([#13](https://github.com/zaebee/codegraph-brain/issues/13)) ([#48](https://github.com/zaebee/codegraph-brain/issues/48)) ([cfca9f3](https://github.com/zaebee/codegraph-brain/commit/cfca9f3455d5646fdfcb95a6b9fd47e57249f387))
* **extractor+resolver:** local type propagation — instance method resolution ([#12](https://github.com/zaebee/codegraph-brain/issues/12)) ([#52](https://github.com/zaebee/codegraph-brain/issues/52)) ([05f7c90](https://github.com/zaebee/codegraph-brain/commit/05f7c905b80bae933adb0623ed740ac4cbfaae3d))
* **extractor:** TypeScript/TSX extractor parity ([#92](https://github.com/zaebee/codegraph-brain/issues/92)) ([f4e7e6b](https://github.com/zaebee/codegraph-brain/commit/f4e7e6b6a48648cbf2704c8231fd576fc3505110))
* FastAPI DI edges — DEPENDS_ON + alias nodes ([#161](https://github.com/zaebee/codegraph-brain/issues/161) slice 1) ([#166](https://github.com/zaebee/codegraph-brain/issues/166)) ([9214bf6](https://github.com/zaebee/codegraph-brain/commit/9214bf6d70d05425b7c81dd3db533d709dc2adac))
* **graph:** symbol-level import edges — IMPORTS_SYMBOL ([#161](https://github.com/zaebee/codegraph-brain/issues/161) slice 2) ([#188](https://github.com/zaebee/codegraph-brain/issues/188)) ([d340d33](https://github.com/zaebee/codegraph-brain/commit/d340d3337679944db42f0b4ff159ce46421dd9cb))
* **guardian:** add /guardian rate &lt;N&gt; comment command ([#110](https://github.com/zaebee/codegraph-brain/issues/110)) ([2d2d80a](https://github.com/zaebee/codegraph-brain/commit/2d2d80afda3b5e96182204ef50c1a62d2c893a82))
* **guardian:** add review quality metrics tracking ([#108](https://github.com/zaebee/codegraph-brain/issues/108)) ([e4413d6](https://github.com/zaebee/codegraph-brain/commit/e4413d6637a8c7deda6e54e79c83723da32284f1))
* **guardian:** adversarial reviewer prompt with severity levels ([#93](https://github.com/zaebee/codegraph-brain/issues/93)) ([1486702](https://github.com/zaebee/codegraph-brain/commit/1486702f31d614ca815e32eb3f46cc1e909a29cd))
* **guardian:** chunked review behind GUARDIAN_FEATURES=chunked (slice 2 of [#154](https://github.com/zaebee/codegraph-brain/issues/154)) ([#159](https://github.com/zaebee/codegraph-brain/issues/159)) ([2030eb8](https://github.com/zaebee/codegraph-brain/commit/2030eb80df0268c078c46483ed45ecb4154407f2))
* **guardian:** connected-subgraph chunker — slice 1 of [#154](https://github.com/zaebee/codegraph-brain/issues/154) ([#157](https://github.com/zaebee/codegraph-brain/issues/157)) ([6178bae](https://github.com/zaebee/codegraph-brain/commit/6178bae14297ec1f1ddb45fb717aa35053041069))
* **guardian:** context upgrades, cross-model skeptic, inline comments ([#156](https://github.com/zaebee/codegraph-brain/issues/156)) ([3d42815](https://github.com/zaebee/codegraph-brain/commit/3d42815f6fe9fa89aca2eb0ecbcb4e4928ca5462))
* **guardian:** graph-aware review — inject impact graphs from graph.db ([#89](https://github.com/zaebee/codegraph-brain/issues/89)) ([fbc7d79](https://github.com/zaebee/codegraph-brain/commit/fbc7d79ccb2895d3e11e20d88f248bf042e77bb9))
* **guardian:** high-precision prompt — cap 5 findings, confidence gate ([#104](https://github.com/zaebee/codegraph-brain/issues/104)) ([e518601](https://github.com/zaebee/codegraph-brain/commit/e518601b0ccd17e85305ee9600b9c112abed811b))
* **guardian:** Mistral provider + auto-select by API key ([#87](https://github.com/zaebee/codegraph-brain/issues/87)) ([6d147ad](https://github.com/zaebee/codegraph-brain/commit/6d147ad4490d6fdd4802661e98ccfdb6941f4dc9))
* **guardian:** polish AI code reviewer — ruff/mypy clean, real Gemini API ([#80](https://github.com/zaebee/codegraph-brain/issues/80)) ([321f036](https://github.com/zaebee/codegraph-brain/commit/321f036cd88a30c3477011d685d5d810a54cc558))
* **guardian:** structured findings + benchmark harness + baseline ([#153](https://github.com/zaebee/codegraph-brain/issues/153)) ([671746a](https://github.com/zaebee/codegraph-brain/commit/671746a89e74d9789399b683e234a92e20673ed9))
* **guardian:** token usage tracking and graph coverage metric ([#97](https://github.com/zaebee/codegraph-brain/issues/97)) ([3e5c32a](https://github.com/zaebee/codegraph-brain/commit/3e5c32aa44c2b6ad885005eeb9da172427492c91))
* incremental ingest with file hashing ([#29](https://github.com/zaebee/codegraph-brain/issues/29)) ([08fab94](https://github.com/zaebee/codegraph-brain/commit/08fab944444e70e7fb6edad67403ba121b922c07))
* MCP server — expose cgis graph operations as agentic tools ([b0447e5](https://github.com/zaebee/codegraph-brain/commit/b0447e56238083a42ef4c192c281255b48adfe0e))
* MCP server — expose cgis graph operations as agentic tools (closes [#27](https://github.com/zaebee/codegraph-brain/issues/27)) ([c3d0f0a](https://github.com/zaebee/codegraph-brain/commit/c3d0f0aed3b02a384c557526fc18c146561ec5d2))
* **mcp:** drift/validate tools + suffix FQN resolution (closes [#145](https://github.com/zaebee/codegraph-brain/issues/145)) ([#162](https://github.com/zaebee/codegraph-brain/issues/162)) ([315278d](https://github.com/zaebee/codegraph-brain/commit/315278dcf649dd2280a14e09a78140b5ee092de7))
* mermaid output format for trace and impact commands ([#11](https://github.com/zaebee/codegraph-brain/issues/11)) ([962745b](https://github.com/zaebee/codegraph-brain/commit/962745b22eb805feedd4c58dfc6041bf9a750005))
* motif-basis fingerprint v2 — triad census, weighted TV drift, quotient k=1 (Part B) ([#144](https://github.com/zaebee/codegraph-brain/issues/144)) ([8a63efe](https://github.com/zaebee/codegraph-brain/commit/8a63efe3f0e6c10e9e22e1d59eccaeb4ed36104e))
* ontology compliance — sync core.yaml with NodeType/EdgeType enums ([#26](https://github.com/zaebee/codegraph-brain/issues/26)) ([4dec94e](https://github.com/zaebee/codegraph-brain/commit/4dec94e91f3232d8b4afb86645e869e9e89b82ba))
* **query:** --min-confidence filter on trace/impact ([#112](https://github.com/zaebee/codegraph-brain/issues/112)) ([#200](https://github.com/zaebee/codegraph-brain/issues/200)) ([9bc20e3](https://github.com/zaebee/codegraph-brain/commit/9bc20e30d170bc263218be9d55daff8d80f1424e))
* **query:** intra-domain fan metrics + self-drift guardrails ([#141](https://github.com/zaebee/codegraph-brain/issues/141)) ([5cb7659](https://github.com/zaebee/codegraph-brain/commit/5cb7659103deba13bc2e43985bb36a524267d336))
* **scripts:** synthetic ideal architecture graph generator ([#132](https://github.com/zaebee/codegraph-brain/issues/132)) ([#136](https://github.com/zaebee/codegraph-brain/issues/136)) ([ad37f17](https://github.com/zaebee/codegraph-brain/commit/ad37f17f8dcef14e7d0777518c0e1b8dad70ad8a))
* semantic dot-separated FQN format ([#28](https://github.com/zaebee/codegraph-brain/issues/28)) ([070cf1f](https://github.com/zaebee/codegraph-brain/commit/070cf1ff38e6e36569ddef046b15f7fdab76f6c9))
* **structure:** cgis structure command — structural hierarchy (UML) view ([#55](https://github.com/zaebee/codegraph-brain/issues/55)) ([8c8243f](https://github.com/zaebee/codegraph-brain/commit/8c8243fa3ae03c21fa4087979242dc6a7682df7a))
* **ui:** React graph visualizer with health scoring and heatmap ([#133](https://github.com/zaebee/codegraph-brain/issues/133)) ([b1421cb](https://github.com/zaebee/codegraph-brain/commit/b1421cbb20d510ca317018e717fc8a0f4c0dff72))
* unified pattern alphabet — profiles, hygiene, params, confidence discount (Part A) ([#143](https://github.com/zaebee/codegraph-brain/issues/143)) ([d9ca22a](https://github.com/zaebee/codegraph-brain/commit/d9ca22a64aa83c0764710acd7a40c6284bb773ed))


### Bug Fixes

* **autodoc:** replace direct push with create-pull-request (branch protection) ([bf0a5f5](https://github.com/zaebee/codegraph-brain/commit/bf0a5f5abddbc006237e9960cf35c729bca4ebb4))
* **diagram:** reduce trace depth to 1 for readable README diagram ([#78](https://github.com/zaebee/codegraph-brain/issues/78)) ([52d1445](https://github.com/zaebee/codegraph-brain/commit/52d14452cbae81ba3430a0a9d0ddefc77df1bec4))
* **extractor:** relative imports in __init__.py + Annotated[] unwrap ([#194](https://github.com/zaebee/codegraph-brain/issues/194)) ([#195](https://github.com/zaebee/codegraph-brain/issues/195)) ([a24dc5c](https://github.com/zaebee/codegraph-brain/commit/a24dc5c188acc3805f23b841883eb91c231138b8))
* **guardian:** add evidence rule to reduce false positives ([#96](https://github.com/zaebee/codegraph-brain/issues/96)) ([af88999](https://github.com/zaebee/codegraph-brain/commit/af88999ee71e2b5ebc7cbc4d12615fff3eccce59))
* **guardian:** carry the footer into the inline review body ([#158](https://github.com/zaebee/codegraph-brain/issues/158)) ([db67e69](https://github.com/zaebee/codegraph-brain/commit/db67e690b16d2fb371161b4d1a8eda981fc49811))
* **guardian:** correct download-artifact SHA (was invalid) ([#114](https://github.com/zaebee/codegraph-brain/issues/114)) ([6e62470](https://github.com/zaebee/codegraph-brain/commit/6e62470d54be18d094e100422dae1df9624bfc2d))
* **guardian:** correct Mistral import path for v2 SDK ([#90](https://github.com/zaebee/codegraph-brain/issues/90)) ([13448e1](https://github.com/zaebee/codegraph-brain/commit/13448e15ea749f2449c1d22e2c15d2d07219d886))
* **guardian:** default model gemini-2.0-flash → gemini-2.5-flash ([#86](https://github.com/zaebee/codegraph-brain/issues/86)) ([725c605](https://github.com/zaebee/codegraph-brain/commit/725c6051597606fba99f0b5b3c34fd5c36cbb796))
* **guardian:** forbid hallucinated line citations in evidence rule ([#98](https://github.com/zaebee/codegraph-brain/issues/98)) ([dc642fb](https://github.com/zaebee/codegraph-brain/commit/dc642fb5dd516c73b8b9dfe6c39c4e65e4adb1f2))
* **guardian:** migrate to google-genai SDK + configurable model ([#84](https://github.com/zaebee/codegraph-brain/issues/84)) ([da633df](https://github.com/zaebee/codegraph-brain/commit/da633dfe3fbd94e589e2674a3d15d1791afe213b))
* **guardian:** pass --repo to gh pr view before checkout ([#124](https://github.com/zaebee/codegraph-brain/issues/124)) ([48fb477](https://github.com/zaebee/codegraph-brain/commit/48fb477219282da45ae3bd5df30d6e2323dbc196))
* **guardian:** use --group instead of --extra for dependency-groups ([#83](https://github.com/zaebee/codegraph-brain/issues/83)) ([40e07e9](https://github.com/zaebee/codegraph-brain/commit/40e07e9f55dc0da3a6dd42f0a3638ce671b9495c))
* **guardian:** use PR base branch for diff instead of hardcoded main ([#123](https://github.com/zaebee/codegraph-brain/issues/123)) ([cda56e6](https://github.com/zaebee/codegraph-brain/commit/cda56e629539030cc2df25aecef8ac1b03e201d0))
* **makefile:** declare all targets as .PHONY ([#135](https://github.com/zaebee/codegraph-brain/issues/135)) ([2c9b991](https://github.com/zaebee/codegraph-brain/commit/2c9b991b41520744f06110ec046d7d416bb05e10))
* **mcp:** broaden exception catch in cgis_ingest to cover DB/IO errors ([a994be7](https://github.com/zaebee/codegraph-brain/commit/a994be75aa7008b34b98da97ee8454186bd8725e))
* **mcp:** protect STDIO stream, add incremental ingest, guard missing DB ([94c84ba](https://github.com/zaebee/codegraph-brain/commit/94c84ba4bccf61693ebe57c6e0b814dac45014fb))
* **mcp:** remove thread-unsafe redirect_stdout, broaden exception catch ([608cc83](https://github.com/zaebee/codegraph-brain/commit/608cc83f74961667175bebbb821c9a05315f116f))
* **metrics:** broaden findings_total regex to match any model category label ([#125](https://github.com/zaebee/codegraph-brain/issues/125)) ([3520556](https://github.com/zaebee/codegraph-brain/commit/3520556dfd6f3e8a0529c779e87a5f94d0019cf9))
* **ontology:** correct FQN convention in core.yaml and harden compliance test ([0f1c128](https://github.com/zaebee/codegraph-brain/commit/0f1c128d6153d17fc46694d367aaf8c342771741))
* path canonicalization — normalize file_paths relative to workspace_root (closes [#33](https://github.com/zaebee/codegraph-brain/issues/33)) ([#34](https://github.com/zaebee/codegraph-brain/issues/34)) ([a6b0306](https://github.com/zaebee/codegraph-brain/commit/a6b03065c030d3f056ad1a31fe5130195f7349f9))
* **pre-commit:** add missing packages to mypy additional_dependencies ([#102](https://github.com/zaebee/codegraph-brain/issues/102)) ([b88ab89](https://github.com/zaebee/codegraph-brain/commit/b88ab891abf8c36d539114a9cbca9ed654e5764a))
* **resolver:** drop phantom-internal local-type resolves + bare inheritance names ([#183](https://github.com/zaebee/codegraph-brain/issues/183)) ([#199](https://github.com/zaebee/codegraph-brain/issues/199)) ([62ee463](https://github.com/zaebee/codegraph-brain/commit/62ee463c0e1b7af4eb21c187ed3175be37c71f94))
* **resolver:** prevent non-deterministic resolution from same-file symbol duplicates ([#46](https://github.com/zaebee/codegraph-brain/issues/46)) ([6aef81c](https://github.com/zaebee/codegraph-brain/commit/6aef81ca8cb2ac5eb8630ad87ca21b977497361c))
* **security:** validate patterns_path in drift service (sonar S2083) ([#167](https://github.com/zaebee/codegraph-brain/issues/167)) ([1f7d35c](https://github.com/zaebee/codegraph-brain/commit/1f7d35c41dc927ad5af1cc5e1f5b9fa183ba5e89))
* use module_fqn for FILE node ID in TS extractor; skip test/spec files in pipeline ([#107](https://github.com/zaebee/codegraph-brain/issues/107)) ([4925bda](https://github.com/zaebee/codegraph-brain/commit/4925bda5d90f5e0be5708df842205b40972470fc))


### Performance Improvements

* **pipeline:** short-circuit no-op incremental re-ingest ([#185](https://github.com/zaebee/codegraph-brain/issues/185)) ([#193](https://github.com/zaebee/codegraph-brain/issues/193)) ([5fbef4f](https://github.com/zaebee/codegraph-brain/commit/5fbef4f0bbd178b1c188ea946bfe32b1fbef6181))


### Documentation

* add UI redesign spec and 4-part implementation plans ([#119](https://github.com/zaebee/codegraph-brain/issues/119)) ([5470045](https://github.com/zaebee/codegraph-brain/commit/5470045e4092b087d3a830bb31c80337a74773d4))
* auto-sync architecture graph and MCP reference ([#109](https://github.com/zaebee/codegraph-brain/issues/109)) ([4d921c5](https://github.com/zaebee/codegraph-brain/commit/4d921c5423318f3df5dbcf028a41b87f2105d6b6))
* auto-sync architecture graph and MCP reference ([#163](https://github.com/zaebee/codegraph-brain/issues/163)) ([47012b2](https://github.com/zaebee/codegraph-brain/commit/47012b27254f457631c2968d232956b96c4ed563))
* auto-sync architecture graph and MCP reference ([#168](https://github.com/zaebee/codegraph-brain/issues/168)) ([a20c2d9](https://github.com/zaebee/codegraph-brain/commit/a20c2d95bd845e69eaa1a54454ef86533f995dc5))
* auto-sync architecture graph and MCP reference ([#184](https://github.com/zaebee/codegraph-brain/issues/184)) ([bb31000](https://github.com/zaebee/codegraph-brain/commit/bb310000a00f9190e2532a6ff0b79ac4ebbb3d2e))
* auto-sync architecture graph and MCP reference ([#189](https://github.com/zaebee/codegraph-brain/issues/189)) ([f3f4ace](https://github.com/zaebee/codegraph-brain/commit/f3f4ace65d2f0278981192d9ab280a8364ba0899))
* auto-sync architecture graph and MCP reference ([#197](https://github.com/zaebee/codegraph-brain/issues/197)) ([bbc1ae0](https://github.com/zaebee/codegraph-brain/commit/bbc1ae0d63090b436b6be0fa75bdfd739a5f82d3))
* auto-sync architecture graph and MCP reference ([#73](https://github.com/zaebee/codegraph-brain/issues/73)) ([2db5202](https://github.com/zaebee/codegraph-brain/commit/2db5202abdebe329caf94d510b306e58b043bed2))
* auto-sync architecture graph and MCP reference ([#76](https://github.com/zaebee/codegraph-brain/issues/76)) ([1eca92f](https://github.com/zaebee/codegraph-brain/commit/1eca92f8a05bfa290943e8cb5c52bfb9d9686995))
* auto-sync architecture graph and MCP reference ([#79](https://github.com/zaebee/codegraph-brain/issues/79)) ([660be23](https://github.com/zaebee/codegraph-brain/commit/660be23a8fc399e5ac27c3f6f6162d76764b30b7))
* auto-sync architecture graph and MCP reference ([#85](https://github.com/zaebee/codegraph-brain/issues/85)) ([464475b](https://github.com/zaebee/codegraph-brain/commit/464475b74c494132a484305c07e46da69f871fca))
* domain pattern fingerprint & drift engine spec ([#138](https://github.com/zaebee/codegraph-brain/issues/138)) ([d4a89bd](https://github.com/zaebee/codegraph-brain/commit/d4a89bd3d46a411b2cdf30655793d2586ed411d8))
* **spec:** guardian sprint design — structured findings, benchmark, context, multi-pass, inline ([#152](https://github.com/zaebee/codegraph-brain/issues/152)) ([dcb4e17](https://github.com/zaebee/codegraph-brain/commit/dcb4e17f1cbafdb6493233636adedb34f383283a))
* **specs:** cgis init-ontology design — auto-proposed patterns.yaml ([#174](https://github.com/zaebee/codegraph-brain/issues/174)) ([#198](https://github.com/zaebee/codegraph-brain/issues/198)) ([30322fc](https://github.com/zaebee/codegraph-brain/commit/30322fc09f4c9e4edeea636878bb3a898e380f34))
* **specs:** drift loud zero-match domains — empty / no_signal ([#178](https://github.com/zaebee/codegraph-brain/issues/178)) ([#190](https://github.com/zaebee/codegraph-brain/issues/190)) ([ea05432](https://github.com/zaebee/codegraph-brain/commit/ea05432570b85ba5c48989ece73e6632a9bbfef6))
* **specs:** FastAPI DI edges design — DEPENDS_ON + alias nodes ([#161](https://github.com/zaebee/codegraph-brain/issues/161) slice 1) ([#164](https://github.com/zaebee/codegraph-brain/issues/164)) ([c67aed2](https://github.com/zaebee/codegraph-brain/commit/c67aed2b76911f9d83ee3cae7775c8d4fe237c69))
* **specs:** resolver split design — IndexBuilder + SymbolResolver ([#115](https://github.com/zaebee/codegraph-brain/issues/115)) ([#169](https://github.com/zaebee/codegraph-brain/issues/169)) ([e67c92f](https://github.com/zaebee/codegraph-brain/commit/e67c92fb59f42b2ee812899d42d1ddc784f56daf))
* **specs:** symbol-level import edges design ([#161](https://github.com/zaebee/codegraph-brain/issues/161) slice 2) ([#187](https://github.com/zaebee/codegraph-brain/issues/187)) ([10408f9](https://github.com/zaebee/codegraph-brain/commit/10408f9cdf976018716ce05404b3e7ded1557b1b))
* **spec:** unified pattern alphabet + motif-basis fingerprint ([#142](https://github.com/zaebee/codegraph-brain/issues/142)) ([aac4711](https://github.com/zaebee/codegraph-brain/commit/aac47113d0fdb5ef2ffa9aed6c0c6d50aac3d792))
* translate and rethink specifications from RU to EN ([66dcbe3](https://github.com/zaebee/codegraph-brain/commit/66dcbe34f14f5e94e2523fad28f5ed994da76992))
* translate and rethink specifications from RU to EN ([585bdef](https://github.com/zaebee/codegraph-brain/commit/585bdefc366e0dbda896ec1f93280355d7b1b979))
