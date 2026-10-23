# Changelog

## [0.37.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.36.0...codegraph-brain-v0.37.0) (2026-10-23)


### Features

* module graph of one package, as Mermaid or JSON ([#743](https://github.com/zaebee/codegraph-brain/issues/743)) ([0ea24a4](https://github.com/zaebee/codegraph-brain/commit/0ea24a4b9fc26043ceff0a8d58c397fa579b3804))


### Documentation

* auto-sync architecture graph and MCP reference ([#744](https://github.com/zaebee/codegraph-brain/issues/744)) ([26fa968](https://github.com/zaebee/codegraph-brain/commit/26fa968e102425544cf1ae7d2505a59f2f46199b))

## [0.36.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.35.0...codegraph-brain-v0.36.0) (2026-10-22)


### Features

* **guardian:** a flagged fourth skeptic verdict, "misdirected" ([#724](https://github.com/zaebee/codegraph-brain/issues/724)) ([f2a69b7](https://github.com/zaebee/codegraph-brain/commit/f2a69b73df62c2d3c65ee96bc9140d2619c53106))
* **guardian:** give the finder diff-check's dependants as leads (flagged) ([#736](https://github.com/zaebee/codegraph-brain/issues/736)) ([f7546cc](https://github.com/zaebee/codegraph-brain/commit/f7546cc88177051436e38ff59e79c4325fa0cfe9))
* **scripts:** replay a skeptic over frozen Martian reviews on their judge grids ([#726](https://github.com/zaebee/codegraph-brain/issues/726)) ([95d26d3](https://github.com/zaebee/codegraph-brain/commit/95d26d3e6115020e92de20d09b8a4c0844b7f2d6))


### Bug Fixes

* **guardian:** review the PR as data, never run its code beside the keys ([#731](https://github.com/zaebee/codegraph-brain/issues/731)) ([113ff81](https://github.com/zaebee/codegraph-brain/commit/113ff81d7ec9525cd7efd54883f193549b36c8cd))

## [0.35.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.34.0...codegraph-brain-v0.35.0) (2026-10-21)


### Features

* **diff-check:** key items tell the reader to check the site's callers ([#710](https://github.com/zaebee/codegraph-brain/issues/710)) ([9906f1c](https://github.com/zaebee/codegraph-brain/commit/9906f1ce4a44920f4c6ffcdde187b8ef5b9d7722))
* **guardian-bench:** a hard budget, and room for medium-effort finders ([#716](https://github.com/zaebee/codegraph-brain/issues/716)) ([aabea46](https://github.com/zaebee/codegraph-brain/commit/aabea468b958ab8f680f12000a500cbcf806154d))
* **guardian-martian:** a hard budget on review and judge ([#717](https://github.com/zaebee/codegraph-brain/issues/717)) ([29efe6b](https://github.com/zaebee/codegraph-brain/commit/29efe6b194ce17670a49110bc74e37fca137f973))
* **guardian:** a two-pass finder that requests its own graph context ([#720](https://github.com/zaebee/codegraph-brain/issues/720)) ([4e254f8](https://github.com/zaebee/codegraph-brain/commit/4e254f80d740df59b528603eef74f539e611d04a))
* **guardian:** anthropic provider for the finder and the skeptic ([#713](https://github.com/zaebee/codegraph-brain/issues/713)) ([bcc3878](https://github.com/zaebee/codegraph-brain/commit/bcc3878cf7b320e8e078ff20a1e47c4948144c1a))
* **query:** cgis diff-check lists what a diff may break, data keys included ([#705](https://github.com/zaebee/codegraph-brain/issues/705)) ([a6f41e2](https://github.com/zaebee/codegraph-brain/commit/a6f41e2e6dfc704881795b648094cc489cf554ad))
* **skeptic-arms:** score skeptic arms on judge-labelled ground-truth matches ([#723](https://github.com/zaebee/codegraph-brain/issues/723)) ([d653107](https://github.com/zaebee/codegraph-brain/commit/d6531074a71f2a0ef3e8ea1008040b3a419c6540))
* **skeptic-arms:** tokens per row and a hard budget stop ([#714](https://github.com/zaebee/codegraph-brain/issues/714)) ([e6f35b1](https://github.com/zaebee/codegraph-brain/commit/e6f35b164e3b26b5efaaffa76a287310a4007435))


### Bug Fixes

* **data-keys:** find owner-api beside the main checkout from a worktree ([#712](https://github.com/zaebee/codegraph-brain/issues/712)) ([5017ee0](https://github.com/zaebee/codegraph-brain/commit/5017ee0b61e07fd786cec92ad9ed67db97fd1a62))
* **guardian:** count gemini thinking tokens as completion ([#715](https://github.com/zaebee/codegraph-brain/issues/715)) ([8f142fb](https://github.com/zaebee/codegraph-brain/commit/8f142fb807bf9fe3a0e2c6b23d62314911062160))


### Documentation

* auto-sync architecture graph and MCP reference ([#708](https://github.com/zaebee/codegraph-brain/issues/708)) ([7306a2e](https://github.com/zaebee/codegraph-brain/commit/7306a2ef184b9a789289c4ff3475cabef544057f))
* auto-sync architecture graph and MCP reference ([#711](https://github.com/zaebee/codegraph-brain/issues/711)) ([8bc1890](https://github.com/zaebee/codegraph-brain/commit/8bc1890139c8f1de0bd4d3b72d31bba0c7f28249))

## [0.34.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.33.0...codegraph-brain-v0.34.0) (2026-10-18)


### Features

* **python:** function values are calls — CV-1 keywords, CV-2, own invokers ([#632](https://github.com/zaebee/codegraph-brain/issues/632)) ([#701](https://github.com/zaebee/codegraph-brain/issues/701)) ([2ed2598](https://github.com/zaebee/codegraph-brain/commit/2ed259834566bc1e106e5557c26b5f39b4c85171))


### Bug Fixes

* **freshness:** a graph built by other cgis extraction code reads STALE ([#618](https://github.com/zaebee/codegraph-brain/issues/618)) ([#700](https://github.com/zaebee/codegraph-brain/issues/700)) ([0ac6946](https://github.com/zaebee/codegraph-brain/commit/0ac69465603eec9ddaf05110ee567630642f9721))


### Documentation

* auto-sync architecture graph and MCP reference ([#704](https://github.com/zaebee/codegraph-brain/issues/704)) ([8b030a0](https://github.com/zaebee/codegraph-brain/commit/8b030a020338a32a6bf23f93e44cae95aa2df061))
* **go:** the caveat says which function values have edges ([#695](https://github.com/zaebee/codegraph-brain/issues/695)) ([#697](https://github.com/zaebee/codegraph-brain/issues/697)) ([878cad8](https://github.com/zaebee/codegraph-brain/commit/878cad801d256d27271bff73de0900daec325f2b))

## [0.33.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.32.0...codegraph-brain-v0.33.0) (2026-10-18)


### Features

* exp4 — Go impact questions measured; Go impact answers are a lead to confirm ([#671](https://github.com/zaebee/codegraph-brain/issues/671)) ([a3ae7c3](https://github.com/zaebee/codegraph-brain/commit/a3ae7c3f599c9296ee185fccf650e7b0c97d39d9))
* **go:** function values handed to an invoker or called where held are calls (core clause CV) ([#686](https://github.com/zaebee/codegraph-brain/issues/686)) ([eab75f8](https://github.com/zaebee/codegraph-brain/commit/eab75f8d243f424d95ec2a069524ce3f4f68a610))


### Bug Fixes

* **go:** a conversion types its value, and a method value on a package variable is held ([#670](https://github.com/zaebee/codegraph-brain/issues/670), [#689](https://github.com/zaebee/codegraph-brain/issues/689)) ([#693](https://github.com/zaebee/codegraph-brain/issues/693)) ([2c67f44](https://github.com/zaebee/codegraph-brain/commit/2c67f442a983787611113f7b007e739349054295))
* **resolver:** a call resolves within its language and scores 1.0 ([#676](https://github.com/zaebee/codegraph-brain/issues/676)) ([8e45fbc](https://github.com/zaebee/codegraph-brain/commit/8e45fbc431f799120e3faad0e1289f927224a935))
* **resolver:** a field's library type is read through its declaring file ([#685](https://github.com/zaebee/codegraph-brain/issues/685)) ([#687](https://github.com/zaebee/codegraph-brain/issues/687)) ([1dcdf7d](https://github.com/zaebee/codegraph-brain/commit/1dcdf7d1b9af50a7e28c725b0ccbdb6433c6b9a5))
* **resolver:** a virtual node takes its caller's language ([#678](https://github.com/zaebee/codegraph-brain/issues/678)) ([cd3f808](https://github.com/zaebee/codegraph-brain/commit/cd3f8083806c32812644bd013355d2081bfe2d25))
* **resolver:** an expanded Go type is classified through the file that declared it ([#688](https://github.com/zaebee/codegraph-brain/issues/688)) ([#691](https://github.com/zaebee/codegraph-brain/issues/691)) ([5d4bcc2](https://github.com/zaebee/codegraph-brain/commit/5d4bcc20c65d177c7921690fb239ec4e3464efaa))
* **resolver:** an unresolved call never joins a real node ([#684](https://github.com/zaebee/codegraph-brain/issues/684)) ([a91dd8f](https://github.com/zaebee/codegraph-brain/commit/a91dd8f5304b9d8db566d94c05f0a099f709b6b1))


### Documentation

* move the guide to AGENTS.md; CLAUDE.md only imports it ([#679](https://github.com/zaebee/codegraph-brain/issues/679)) ([549f4cf](https://github.com/zaebee/codegraph-brain/commit/549f4cf9fc6c818af8e8b445bfaeffd0b8253618))

## [0.32.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.31.0...codegraph-brain-v0.32.0) (2026-10-16)


### Features

* **go:** calls on a call's result, an assertion or an index get an edge ([#662](https://github.com/zaebee/codegraph-brain/issues/662)) ([79b1e0a](https://github.com/zaebee/codegraph-brain/commit/79b1e0a8135a0b41f292bd434148abf6d0f7a1a3))
* **go:** read Go files for one build, linux/amd64, and name the rest ([#667](https://github.com/zaebee/codegraph-brain/issues/667)) ([3711351](https://github.com/zaebee/codegraph-brain/commit/37113516c4932a5dc4cc30bb139bcac041de5480))
* **go:** type channel, range, index, assertion and package-variable bindings, with block scope ([#660](https://github.com/zaebee/codegraph-brain/issues/660)) ([604d76d](https://github.com/zaebee/codegraph-brain/commit/604d76d14a4cfe9d60f4cead8dc8b80b7712d317))
* **resolver:** typed receivers reach promoted methods and follow field hops ([#656](https://github.com/zaebee/codegraph-brain/issues/656)) ([b957aba](https://github.com/zaebee/codegraph-brain/commit/b957aba3c16daaa62bd48f50fbced39ece6e6c31))


### Bug Fixes

* **go:** CALLS edges carry their file and line ([#663](https://github.com/zaebee/codegraph-brain/issues/663)) ([#665](https://github.com/zaebee/codegraph-brain/issues/665)) ([8739aa0](https://github.com/zaebee/codegraph-brain/commit/8739aa00e17def9e14c54c3a8c3240f085b3e568))


### Documentation

* auto-sync architecture graph and MCP reference ([#664](https://github.com/zaebee/codegraph-brain/issues/664)) ([67e01f5](https://github.com/zaebee/codegraph-brain/commit/67e01f581c7f66222a021d3600ed4e084343d070))

## [0.31.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.30.0...codegraph-brain-v0.31.0) (2026-10-16)


### Features

* **bench:** score Go method spellings ([#653](https://github.com/zaebee/codegraph-brain/issues/653)) ([3eccec7](https://github.com/zaebee/codegraph-brain/commit/3eccec74a01af9c128431714d47270badf739e7c))
* **go:** interface satisfaction compares arity, not just names ([#651](https://github.com/zaebee/codegraph-brain/issues/651)) ([05a8dd2](https://github.com/zaebee/codegraph-brain/commit/05a8dd2b5d7486733d74d9c139519c873a43d523))
* **go:** interface satisfaction dispatches; embedding no longer does ([#647](https://github.com/zaebee/codegraph-brain/issues/647)) ([ff785f7](https://github.com/zaebee/codegraph-brain/commit/ff785f7e40a91e159266d1c7cb38ed912a1ed676))
* **query:** a DISPATCHES hop costs no traversal depth ([#649](https://github.com/zaebee/codegraph-brain/issues/649)) ([b5c9ed9](https://github.com/zaebee/codegraph-brain/commit/b5c9ed91bec8b7c857e17f974f5d351c0c745094))
* **query:** coverage note screens test callers and says where each gap is ([#644](https://github.com/zaebee/codegraph-brain/issues/644)) ([814a679](https://github.com/zaebee/codegraph-brain/commit/814a67904f980048f9d699f14408b6f4a3732c4f))


### Bug Fixes

* **logging:** the CLI and the MCP server log to stderr ([#619](https://github.com/zaebee/codegraph-brain/issues/619)) ([#622](https://github.com/zaebee/codegraph-brain/issues/622)) ([3fc1db2](https://github.com/zaebee/codegraph-brain/commit/3fc1db20d5c8a1cdb5793d357550c7854f499161))
* **resolver:** an Any-typed receiver declares no type ([#638](https://github.com/zaebee/codegraph-brain/issues/638)) ([4cb43b6](https://github.com/zaebee/codegraph-brain/commit/4cb43b676e43a82b3a9edf3a6175e34623b68bba))


### Documentation

* **lab-notes:** exp1 — change radius and indirection vs agent cost (pre-registered) ([#633](https://github.com/zaebee/codegraph-brain/issues/633)) ([b6c002c](https://github.com/zaebee/codegraph-brain/commit/b6c002c9522a863b2412c06ca5130f622887ee40))
* **lab-notes:** exp2 — indirection at fixed radius and answer size, on cgis (pre-registered) ([#634](https://github.com/zaebee/codegraph-brain/issues/634)) ([c74c90d](https://github.com/zaebee/codegraph-brain/commit/c74c90d6dd4e13d5fcd52339ed7b699bce6fa535))
* **lab-notes:** exp3 — does cgis help an agent (pre-registered, kill criterion) ([#635](https://github.com/zaebee/codegraph-brain/issues/635)) ([507ada5](https://github.com/zaebee/codegraph-brain/commit/507ada5561147b7ec94fab9e8a3a21eb673755eb))
* **lab-notes:** exp3b — rerun after the [#636](https://github.com/zaebee/codegraph-brain/issues/636) fix (pre-registered) ([#640](https://github.com/zaebee/codegraph-brain/issues/640)) ([c5cb290](https://github.com/zaebee/codegraph-brain/commit/c5cb290eb993cbf7fcee78c092d7f6bb61aa22b0))

## [0.30.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.29.0...codegraph-brain-v0.30.0) (2026-10-15)


### Features

* **languages:** Go field and call-result types resolve receiver calls ([#608](https://github.com/zaebee/codegraph-brain/issues/608)) ([#614](https://github.com/zaebee/codegraph-brain/issues/614)) ([96ec5f3](https://github.com/zaebee/codegraph-brain/commit/96ec5f380d52ba1560c63b8b84af673627bc88d0))
* **languages:** step 7b — the Go pilot ([#607](https://github.com/zaebee/codegraph-brain/issues/607)) ([4a62703](https://github.com/zaebee/codegraph-brain/commit/4a62703f41a4dfb74d1f96bee132b3f78f84c895))
* **pipeline:** a graph built by other extraction code is rebuilt in full ([#617](https://github.com/zaebee/codegraph-brain/issues/617)) ([dc33153](https://github.com/zaebee/codegraph-brain/commit/dc331531556cc2dc49c8adfacbec079584953eb8))
* **python:** FastAPI handlers play the ROUTE_HANDLER role ([#566](https://github.com/zaebee/codegraph-brain/issues/566)) ([#616](https://github.com/zaebee/codegraph-brain/issues/616)) ([9098ccd](https://github.com/zaebee/codegraph-brain/commit/9098ccd87ef08fbb3139243fda426b62e8d1b32b))


### Bug Fixes

* **docs:** coverage claims name Go — the MCP instructions are built from the registry ([#615](https://github.com/zaebee/codegraph-brain/issues/615)) ([8ea6362](https://github.com/zaebee/codegraph-brain/commit/8ea636239122b24c4a42b2a65517ebb12dad1b03))


### Documentation

* auto-sync architecture graph and MCP reference ([#598](https://github.com/zaebee/codegraph-brain/issues/598)) ([3feb1ac](https://github.com/zaebee/codegraph-brain/commit/3feb1ac40b1896acb0e57109c9bd1f2bf941d7bc))
* auto-sync architecture graph and MCP reference ([#620](https://github.com/zaebee/codegraph-brain/issues/620)) ([3f36e15](https://github.com/zaebee/codegraph-brain/commit/3f36e15c30dca7280b57bebd70a8793764cd872e))
* **spec:** language packs and a two-plane topology ([#593](https://github.com/zaebee/codegraph-brain/issues/593)) ([d9e7d85](https://github.com/zaebee/codegraph-brain/commit/d9e7d852b17d4cab4a2a4a0f2519f11f8de9236e))

## [0.29.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.28.0...codegraph-brain-v0.29.0) (2026-10-14)


### Features

* **bench:** cgis-narrow arm — the cgis arm with three tools listed ([#586](https://github.com/zaebee/codegraph-brain/issues/586)) ([5a5c836](https://github.com/zaebee/codegraph-brain/commit/5a5c836e2fda1f41e6420e0d1160a56a0859ef94))
* **mcp:** agent-sized cgis_analyze_impact — no test callers, files as dead ends, node budget ([#563](https://github.com/zaebee/codegraph-brain/issues/563)) ([d173d57](https://github.com/zaebee/codegraph-brain/commit/d173d5791e30983b58e7cbe7fb91cac8c7059154))
* **mcp:** output_format="text" for traversals; trace_flow gets impact's node budget and test screen ([#581](https://github.com/zaebee/codegraph-brain/issues/581)) ([a1dd2b8](https://github.com/zaebee/codegraph-brain/commit/a1dd2b871d34c22e29f25103b9ee349a004ea28a))
* **mcp:** server instructions in initialize, CGIS_MCP_TOOLS allowlist ([#565](https://github.com/zaebee/codegraph-brain/issues/565)) ([3c37874](https://github.com/zaebee/codegraph-brain/commit/3c37874f78e4a3d439be153641afe1fc19a32f03))
* **query:** resolve an FQN given with the directories above the graph ([#571](https://github.com/zaebee/codegraph-brain/issues/571)) ([ea43ba9](https://github.com/zaebee/codegraph-brain/commit/ea43ba9f5451e0ee55575570eb8a5341eb906115))
* **resolver:** DISPATCHES edges from a base-class method to each override ([#575](https://github.com/zaebee/codegraph-brain/issues/575)) ([827143e](https://github.com/zaebee/codegraph-brain/commit/827143ee2a13866602daaea84f1142106c69d49d))
* **resolver:** type a parameter annotated with an Annotated[T, Depends(...)] alias as T ([#572](https://github.com/zaebee/codegraph-brain/issues/572)) ([fc33b20](https://github.com/zaebee/codegraph-brain/commit/fc33b202b607aecda9d3b317edc1d16f2ba350c6))
* **workspaces:** read pnpm-workspace.yaml above the ingest root ([#591](https://github.com/zaebee/codegraph-brain/issues/591)) ([b0d67f8](https://github.com/zaebee/codegraph-brain/commit/b0d67f8124da3c414b852f045f6127ab2eec5c3e))


### Bug Fixes

* **bench:** agent A/B runner survives a failed session and continues run numbers ([#578](https://github.com/zaebee/codegraph-brain/issues/578)) ([bc303eb](https://github.com/zaebee/codegraph-brain/commit/bc303eb82e3ef25c01093452c2d68e8c3a53d1d9))
* **bench:** guard — no false refusal on a grep for an import, no bypass through env/newline/prefix ([#590](https://github.com/zaebee/codegraph-brain/issues/590)) ([73ee4e2](https://github.com/zaebee/codegraph-brain/commit/73ee4e260cf3a65a31630f4cc6a46b618872ce7f))
* **ingest:** read the tsconfig.json and package.json above a sub-directory ingest root ([#560](https://github.com/zaebee/codegraph-brain/issues/560)) ([9722f8c](https://github.com/zaebee/codegraph-brain/commit/9722f8c18da97cba20400cbd109fd8fea684a5e3))
* **query:** one effective_prefix rule for every FQN-prefix entry point ([#585](https://github.com/zaebee/codegraph-brain/issues/585)) ([b5f10b3](https://github.com/zaebee/codegraph-brain/commit/b5f10b360ab021ba2dcd8dd9a211b222c989e183))
* **resolver:** TypeScript imports of declared npm packages are EXTERNAL ([#557](https://github.com/zaebee/codegraph-brain/issues/557)) ([73d380a](https://github.com/zaebee/codegraph-brain/commit/73d380acb915671e9597fc01eaf509ba29e78a8b))


### Documentation

* auto-sync architecture graph and MCP reference ([#558](https://github.com/zaebee/codegraph-brain/issues/558)) ([ee275a4](https://github.com/zaebee/codegraph-brain/commit/ee275a412b9af8b2d900e16b84335a343f68a7fa))
* auto-sync architecture graph and MCP reference ([#564](https://github.com/zaebee/codegraph-brain/issues/564)) ([50637b3](https://github.com/zaebee/codegraph-brain/commit/50637b3b8d01cb4be01cbae3710f21a1cb5f1b02))
* auto-sync architecture graph and MCP reference ([#573](https://github.com/zaebee/codegraph-brain/issues/573)) ([225499d](https://github.com/zaebee/codegraph-brain/commit/225499dd1a17163160e270b9dbeb73a7ad376b7d))
* **bench:** agent A/B after [#568](https://github.com/zaebee/codegraph-brain/issues/568) and [#550](https://github.com/zaebee/codegraph-brain/issues/550) — cgis-forced at $0.184 against control $0.298 ([#574](https://github.com/zaebee/codegraph-brain/issues/574)) ([8e0ef37](https://github.com/zaebee/codegraph-brain/commit/8e0ef37b76fffc01e32338c67325535f8cb501f4))
* **bench:** agent A/B on a task no fix was tuned on (occupied_window) ([#576](https://github.com/zaebee/codegraph-brain/issues/576)) ([a6eb679](https://github.com/zaebee/codegraph-brain/commit/a6eb679b51ae7fd3f52c73eb2da005857f2e6126))
* **bench:** agent A/B on three deep-chain cgis tasks — impact pays, trace_flow's 32k answer does not ([#579](https://github.com/zaebee/codegraph-brain/issues/579)) ([eb7e951](https://github.com/zaebee/codegraph-brain/commit/eb7e9516a8e952fdbd2266c43accfee1a16109e7))
* **bench:** agent A/B results for owner-impact-credit-wallet after [#563](https://github.com/zaebee/codegraph-brain/issues/563) and [#565](https://github.com/zaebee/codegraph-brain/issues/565) ([#570](https://github.com/zaebee/codegraph-brain/issues/570)) ([ae84feb](https://github.com/zaebee/codegraph-brain/commit/ae84febcc2621a92d00233e81e994171b6b14cd8))
* **bench:** flow A/B after [#581](https://github.com/zaebee/codegraph-brain/issues/581) — text cut the answer 4x and the cost by a third; precision unchanged ([#583](https://github.com/zaebee/codegraph-brain/issues/583)) ([e36c850](https://github.com/zaebee/codegraph-brain/commit/e36c85099a85300f46a9b6fa28d375fa68f67227))
* **bench:** unprompted agents do not call cgis — a short menu changes nothing, one instruction line 2 of 6 ([#587](https://github.com/zaebee/codegraph-brain/issues/587)) ([6534f74](https://github.com/zaebee/codegraph-brain/commit/6534f74f23fcd3d1193bda2201bbf598959cac8e))

## [0.28.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.27.0...codegraph-brain-v0.28.0) (2026-10-02)


### Features

* **bench:** agent A/B harness for cgis vs no cgis ([#547](https://github.com/zaebee/codegraph-brain/issues/547)) ([788d67f](https://github.com/zaebee/codegraph-brain/commit/788d67f230f1129379afb08596da5a4ef9e1c302))
* **bench:** cgis-instructed and cgis-forced arms, four multi-hop impact tasks ([#549](https://github.com/zaebee/codegraph-brain/issues/549)) ([deef289](https://github.com/zaebee/codegraph-brain/commit/deef289fe9a9a302088661acbf3c6f3d33fc50fe))

## [0.27.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.26.0...codegraph-brain-v0.27.0) (2026-10-02)


### Features

* **mcp:** opt-in refresh of a stale graph before read tools answer ([#538](https://github.com/zaebee/codegraph-brain/issues/538)) ([b26a2c8](https://github.com/zaebee/codegraph-brain/commit/b26a2c8d335fbb2f19a217fcaced715f16454664))
* **orphans:** consider module-level classes only by default ([#432](https://github.com/zaebee/codegraph-brain/issues/432)) ([#534](https://github.com/zaebee/codegraph-brain/issues/534)) ([731d93a](https://github.com/zaebee/codegraph-brain/commit/731d93a8b81fd02fe6b5e71892a408341d034239))
* **suggest-packages:** judge a package by its direct children ([#446](https://github.com/zaebee/codegraph-brain/issues/446)) ([#536](https://github.com/zaebee/codegraph-brain/issues/536)) ([fd6f05b](https://github.com/zaebee/codegraph-brain/commit/fd6f05b003ea006392e6384170a4bead227f5e85))


### Bug Fixes

* **freshness:** report a write made during ingest as stale ([#535](https://github.com/zaebee/codegraph-brain/issues/535)) ([4121919](https://github.com/zaebee/codegraph-brain/commit/4121919148affbad202156d43d01fca458beca23))
* **resolver:** classify declared Python dependencies as external ([#495](https://github.com/zaebee/codegraph-brain/issues/495)) ([#531](https://github.com/zaebee/codegraph-brain/issues/531)) ([ca4c01d](https://github.com/zaebee/codegraph-brain/commit/ca4c01d1ffa8e8c754317af73ffcf06d534a61d7))


### Documentation

* auto-sync architecture graph and MCP reference ([#532](https://github.com/zaebee/codegraph-brain/issues/532)) ([13c90fd](https://github.com/zaebee/codegraph-brain/commit/13c90fd3ebcc38271b697f514d292c7033d4f81d))
* auto-sync architecture graph and MCP reference ([#537](https://github.com/zaebee/codegraph-brain/issues/537)) ([c03f60d](https://github.com/zaebee/codegraph-brain/commit/c03f60d4211e3029d02a624ef9d6b4fa92f184df))
* **onboarding:** refresh the graph from hooks instead of a watcher ([#541](https://github.com/zaebee/codegraph-brain/issues/541)) ([809169d](https://github.com/zaebee/codegraph-brain/commit/809169d19797e280b0810f049215950b3eedf1df)), closes [#175](https://github.com/zaebee/codegraph-brain/issues/175)

## [0.26.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.25.4...codegraph-brain-v0.26.0) (2026-10-02)


### Features

* **metrics:** LCOM4 class cohesion ([#521](https://github.com/zaebee/codegraph-brain/issues/521)) ([ed46366](https://github.com/zaebee/codegraph-brain/commit/ed46366f57af23e8b4aba66b6346c246d47a1c24))
* **metrics:** per-file afferent/efferent coupling and instability ([#520](https://github.com/zaebee/codegraph-brain/issues/520)) ([32393e6](https://github.com/zaebee/codegraph-brain/commit/32393e63f4f8dc9a193cfd6ccba4a18a0d52268a))
* **metrics:** report the unresolved-edge share beside the rankings ([#519](https://github.com/zaebee/codegraph-brain/issues/519)) ([97699f8](https://github.com/zaebee/codegraph-brain/commit/97699f8bd52eb97c6dc32cbf5d6fcc1bfa752d1b)), closes [#451](https://github.com/zaebee/codegraph-brain/issues/451)


### Bug Fixes

* **drift:** measure unresolved_ratio on stored graphs, carry it to the quotient ([#149](https://github.com/zaebee/codegraph-brain/issues/149)) ([#526](https://github.com/zaebee/codegraph-brain/issues/526)) ([80c3b5a](https://github.com/zaebee/codegraph-brain/commit/80c3b5a0a41bd45ab37cc24d2f4ae06fe24e752c))
* **guardian:** record GUARDIAN_FEATURES on each review row ([#516](https://github.com/zaebee/codegraph-brain/issues/516)) ([007408c](https://github.com/zaebee/codegraph-brain/commit/007408c0f96e9b175b389db773e51d6838b0738b))
* **resolver:** resolve TypeScript imports through tsconfig paths aliases ([#513](https://github.com/zaebee/codegraph-brain/issues/513)) ([94219d7](https://github.com/zaebee/codegraph-brain/commit/94219d7e136195bad4eac69a72e25dfd2b3e621f))
* **resolver:** strip only corroborated layout prefixes when reconciling an FQN ([#319](https://github.com/zaebee/codegraph-brain/issues/319)) ([#527](https://github.com/zaebee/codegraph-brain/issues/527)) ([5fe77f4](https://github.com/zaebee/codegraph-brain/commit/5fe77f47c0ac251a73c8820f91f1cc1f1bc13020))
* **ui:** fit the view after React Flow renders the new layout ([#523](https://github.com/zaebee/codegraph-brain/issues/523)) ([7be630f](https://github.com/zaebee/codegraph-brain/commit/7be630f5d3734b3d2b7ff23d032380af4a90f19c))


### Documentation

* auto-sync architecture graph and MCP reference ([#514](https://github.com/zaebee/codegraph-brain/issues/514)) ([5abdebd](https://github.com/zaebee/codegraph-brain/commit/5abdebd482071e8e1f24c19559c984fc874d86b6))

## [0.25.4](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.25.3...codegraph-brain-v0.25.4) (2026-09-25)


### Bug Fixes

* **resolver:** resolve TypeScript imports of declaration files ([#511](https://github.com/zaebee/codegraph-brain/issues/511)) ([db8c578](https://github.com/zaebee/codegraph-brain/commit/db8c578272671c0f06f454c4402ae2f60b05a93d)), closes [#507](https://github.com/zaebee/codegraph-brain/issues/507)
* **resolver:** resolve TypeScript imports of workspace packages ([#506](https://github.com/zaebee/codegraph-brain/issues/506)) ([50c353f](https://github.com/zaebee/codegraph-brain/commit/50c353fea6382b60aa0356fe0cb5205a1054d859))


### Documentation

* auto-sync architecture graph and MCP reference ([#509](https://github.com/zaebee/codegraph-brain/issues/509)) ([8429fd4](https://github.com/zaebee/codegraph-brain/commit/8429fd41fc3b0f46d73286a5ce530d11fcd0df7f))

## [0.25.3](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.25.2...codegraph-brain-v0.25.3) (2026-09-18)


### Bug Fixes

* **analyze:** TYPE_CHECKING imports are not a runtime dependency ([#501](https://github.com/zaebee/codegraph-brain/issues/501)) ([a6d09fe](https://github.com/zaebee/codegraph-brain/commit/a6d09fe21c5fa4dd7611ade50aea239dccde6960))


### Documentation

* auto-sync architecture graph and MCP reference ([#502](https://github.com/zaebee/codegraph-brain/issues/502)) ([a1156ec](https://github.com/zaebee/codegraph-brain/commit/a1156ec4b8cd4a4d8adfdc610dc2a31c434e11d7))

## [0.25.2](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.25.1...codegraph-brain-v0.25.2) (2026-09-18)


### Bug Fixes

* **resolver:** module imports reconcile the layout prefix ([#498](https://github.com/zaebee/codegraph-brain/issues/498)) ([25b7f82](https://github.com/zaebee/codegraph-brain/commit/25b7f82e5011c654de9e4cf9a196995426d4e266))

## [0.25.1](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.25.0...codegraph-brain-v0.25.1) (2026-09-17)


### Bug Fixes

* **resolver:** a missing internal symbol is UNKNOWN, not a resolved internal call ([#493](https://github.com/zaebee/codegraph-brain/issues/493)) ([cec7ce3](https://github.com/zaebee/codegraph-brain/commit/cec7ce342b1e3021d3bc29add37f3ef6c6a3965b))


### Documentation

* auto-sync architecture graph and MCP reference ([#496](https://github.com/zaebee/codegraph-brain/issues/496)) ([b67642f](https://github.com/zaebee/codegraph-brain/commit/b67642fd88f3a2a0a9ce662514c12229159c64b4))

## [0.25.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.24.0...codegraph-brain-v0.25.0) (2026-09-17)


### Features

* **query:** get_structure answers a package prefix with its modules ([#490](https://github.com/zaebee/codegraph-brain/issues/490)) ([2c6119d](https://github.com/zaebee/codegraph-brain/commit/2c6119d3528789775bd968b8b25892421d037eae))


### Documentation

* auto-sync architecture graph and MCP reference ([#491](https://github.com/zaebee/codegraph-brain/issues/491)) ([0a59947](https://github.com/zaebee/codegraph-brain/commit/0a59947f98c32aba31b9b9ef4c562f82cfd6fbd8))

## [0.24.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.23.4...codegraph-brain-v0.24.0) (2026-09-17)


### Features

* **mcp:** cgis_overview — a starting map when you have no FQN yet ([#486](https://github.com/zaebee/codegraph-brain/issues/486)) ([1580cfe](https://github.com/zaebee/codegraph-brain/commit/1580cfe53325241e149ba3bbb9388c4ddc7718d7))


### Documentation

* auto-sync architecture graph and MCP reference ([#488](https://github.com/zaebee/codegraph-brain/issues/488)) ([9f169ce](https://github.com/zaebee/codegraph-brain/commit/9f169ce364f0d778c0c59b4de607a3433b800dbd))

## [0.23.4](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.23.3...codegraph-brain-v0.23.4) (2026-09-17)


### Bug Fixes

* **mcp:** trace_flow/analyze_impact default to the CLI's view ([#482](https://github.com/zaebee/codegraph-brain/issues/482)) ([8877051](https://github.com/zaebee/codegraph-brain/commit/8877051d9b6f90e6df2a25480c3f608cdacb5f58))


### Documentation

* auto-sync architecture graph and MCP reference ([#484](https://github.com/zaebee/codegraph-brain/issues/484)) ([2f507b0](https://github.com/zaebee/codegraph-brain/commit/2f507b004c232dae3d90577f73d763f4d27be357))

## [0.23.3](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.23.2...codegraph-brain-v0.23.3) (2026-09-17)


### Documentation

* auto-sync architecture graph and MCP reference ([#480](https://github.com/zaebee/codegraph-brain/issues/480)) ([5379cce](https://github.com/zaebee/codegraph-brain/commit/5379cce927b5fade5e2371503a0cd9e1f8041fc7))
* **mcp:** say when to use each overlapping graph tool ([#477](https://github.com/zaebee/codegraph-brain/issues/477)) ([f33d764](https://github.com/zaebee/codegraph-brain/commit/f33d764ebdf217359964347aaa1137dc18ced513))

## [0.23.2](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.23.1...codegraph-brain-v0.23.2) (2026-09-17)


### Bug Fixes

* **cli:** impact/trace list each neighbour once, JSON each edge once ([#475](https://github.com/zaebee/codegraph-brain/issues/475)) ([9c93b4b](https://github.com/zaebee/codegraph-brain/commit/9c93b4ba5748f99fe2aeb43d698c9d9628e6295a))

## [0.23.1](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.23.0...codegraph-brain-v0.23.1) (2026-09-17)


### Bug Fixes

* **audit:** an audit that selects no sources is an error, not an empty pass ([#472](https://github.com/zaebee/codegraph-brain/issues/472)) ([9f93010](https://github.com/zaebee/codegraph-brain/commit/9f93010c286d0b189f3c3350833a5cf557316d8f))


### Documentation

* auto-sync architecture graph and MCP reference ([#468](https://github.com/zaebee/codegraph-brain/issues/468)) ([4c02171](https://github.com/zaebee/codegraph-brain/commit/4c021719776d00ae2f3539409ba847995f79c48e))
* auto-sync architecture graph and MCP reference ([#473](https://github.com/zaebee/codegraph-brain/issues/473)) ([567b091](https://github.com/zaebee/codegraph-brain/commit/567b09190c61ab5fc2b5b26c0b445bff2cbe5a12))

## [0.23.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.22.0...codegraph-brain-v0.23.0) (2026-09-17)


### Features

* **mcp:** describe every tool parameter and announce the server version ([#466](https://github.com/zaebee/codegraph-brain/issues/466)) ([1863a11](https://github.com/zaebee/codegraph-brain/commit/1863a111a752d01303de482b88cee46322184b16))

## [0.22.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.21.0...codegraph-brain-v0.22.0) (2026-09-17)


### Features

* **mcp:** publishable to the official MCP Registry ([#464](https://github.com/zaebee/codegraph-brain/issues/464)) ([328ef6e](https://github.com/zaebee/codegraph-brain/commit/328ef6e655fa9324c2dc9940928edc065c28561e))


### Documentation

* **readme:** lead with what CGIS does, for whom, and how to try it ([#462](https://github.com/zaebee/codegraph-brain/issues/462)) ([3288c3e](https://github.com/zaebee/codegraph-brain/commit/3288c3ea44cf0099651bb8b9a0224b5c0044b16f))

## [0.21.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.20.0...codegraph-brain-v0.21.0) (2026-09-16)


### Features

* **resolver:** resolve JS/TS runtime globals into js_builtins.* ([#111](https://github.com/zaebee/codegraph-brain/issues/111)) ([#453](https://github.com/zaebee/codegraph-brain/issues/453)) ([84e790b](https://github.com/zaebee/codegraph-brain/commit/84e790b404f7a515e048f7a2370b8865b71912ff))


### Bug Fixes

* **cli:** a full ingest into a database records file hashes ([#461](https://github.com/zaebee/codegraph-brain/issues/461)) ([e0a4a5e](https://github.com/zaebee/codegraph-brain/commit/e0a4a5ec34f8b0b34220f3f318ce0edc99ed1d9b))
* **pipeline:** rebuild when a change alters what other files resolve against ([#38](https://github.com/zaebee/codegraph-brain/issues/38)) ([#458](https://github.com/zaebee/codegraph-brain/issues/458)) ([8126fd6](https://github.com/zaebee/codegraph-brain/commit/8126fd6377c82613f9f8a76f33253a8cd9e636f0))
* **resolver:** Python stdlib names are STDLIB only in Python sources ([#454](https://github.com/zaebee/codegraph-brain/issues/454)) ([#457](https://github.com/zaebee/codegraph-brain/issues/457)) ([15e7ba1](https://github.com/zaebee/codegraph-brain/commit/15e7ba17ae49e2e3c10a2ef9f6cf1f11e663edfb))


### Documentation

* auto-sync architecture graph and MCP reference ([#456](https://github.com/zaebee/codegraph-brain/issues/456)) ([ae24054](https://github.com/zaebee/codegraph-brain/commit/ae24054361935dbb1a1f9471eea7b4b949ab8a10))
* auto-sync architecture graph and MCP reference ([#460](https://github.com/zaebee/codegraph-brain/issues/460)) ([a341063](https://github.com/zaebee/codegraph-brain/commit/a3410631511802bdc9851decadbbbf25b40d8d22))

## [0.20.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.19.0...codegraph-brain-v0.20.0) (2026-09-11)


### Features

* **query:** a traversal says how much of its neighbourhood resolved ([#201](https://github.com/zaebee/codegraph-brain/issues/201)) ([#448](https://github.com/zaebee/codegraph-brain/issues/448)) ([0c63469](https://github.com/zaebee/codegraph-brain/commit/0c634697789f5264316c8a218e993f13a9a0f73f))


### Documentation

* auto-sync architecture graph and MCP reference ([#449](https://github.com/zaebee/codegraph-brain/issues/449)) ([e9eccae](https://github.com/zaebee/codegraph-brain/commit/e9eccae8d43e8dc264ca87f8ea0a45752471f079))

## [0.19.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.18.0...codegraph-brain-v0.19.0) (2026-09-09)


### Features

* **freshness:** every query says when the graph is out of date ([#175](https://github.com/zaebee/codegraph-brain/issues/175) part 2) ([#443](https://github.com/zaebee/codegraph-brain/issues/443)) ([0d6d197](https://github.com/zaebee/codegraph-brain/commit/0d6d197133c1315698b10a223aaf8efb47784578))
* **metrics:** --scope &lt;prefix&gt; to focus rankings on one subtree ([#239](https://github.com/zaebee/codegraph-brain/issues/239)) ([#439](https://github.com/zaebee/codegraph-brain/issues/439)) ([59605d3](https://github.com/zaebee/codegraph-brain/commit/59605d361f95aeea18b8272183f20938c0ca10b8))
* **orphans:** generated classes are noise, not findings — hide them by default ([#432](https://github.com/zaebee/codegraph-brain/issues/432)) ([#441](https://github.com/zaebee/codegraph-brain/issues/441)) ([52e0de1](https://github.com/zaebee/codegraph-brain/commit/52e0de1e666612f72d5a58eaad9509252db098e2))


### Bug Fixes

* **extractor:** `with X() as y` records y's type ([#444](https://github.com/zaebee/codegraph-brain/issues/444)) ([#445](https://github.com/zaebee/codegraph-brain/issues/445)) ([529b2e2](https://github.com/zaebee/codegraph-brain/commit/529b2e2239ab62a14211d95291afb1d7a6666cfc))
* **resolver:** only a head-discarding strip is forbidden a foreign head ([#435](https://github.com/zaebee/codegraph-brain/issues/435)) ([#437](https://github.com/zaebee/codegraph-brain/issues/437)) ([3e5309a](https://github.com/zaebee/codegraph-brain/commit/3e5309ac6e65cfdc5174dc70c6ae9d4b5036d92a))
* **suggest-packages:** name a member by its path under the package ([#446](https://github.com/zaebee/codegraph-brain/issues/446)) ([#447](https://github.com/zaebee/codegraph-brain/issues/447)) ([08af353](https://github.com/zaebee/codegraph-brain/commit/08af353f88b0026f799eb2cb2f9ed5f0c9733d22))


### Documentation

* auto-sync architecture graph and MCP reference ([#442](https://github.com/zaebee/codegraph-brain/issues/442)) ([47a6aab](https://github.com/zaebee/codegraph-brain/commit/47a6aab38225f8e8936abd9d9e72b9449f10afe2))

## [0.18.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.17.0...codegraph-brain-v0.18.0) (2026-09-06)


### Features

* **extractors:** a class named as a value is a reference (D10, [#415](https://github.com/zaebee/codegraph-brain/issues/415)) ([#430](https://github.com/zaebee/codegraph-brain/issues/430)) ([aea4076](https://github.com/zaebee/codegraph-brain/commit/aea4076b45d0761f50bb989936e89c8054402372))
* **extractors:** a decorator's arguments are edges, and a decorated class keeps its own ([#429](https://github.com/zaebee/codegraph-brain/issues/429)) ([#434](https://github.com/zaebee/codegraph-brain/issues/434)) ([fd0c63f](https://github.com/zaebee/codegraph-brain/commit/fd0c63f1f887a0b65a433d01571654ae79419ef3))
* **query:** orphan classes — what production no longer builds ([#415](https://github.com/zaebee/codegraph-brain/issues/415)) ([#431](https://github.com/zaebee/codegraph-brain/issues/431)) ([3b4db3c](https://github.com/zaebee/codegraph-brain/commit/3b4db3cb231d43ae388b080d64d19b3244739017))
* **resolver:** resolve self.&lt;attr&gt;.&lt;method&gt; through the declared receiver ([#414](https://github.com/zaebee/codegraph-brain/issues/414)) ([#423](https://github.com/zaebee/codegraph-brain/issues/423)) ([a1c94a2](https://github.com/zaebee/codegraph-brain/commit/a1c94a2e4629da955deef761467730a7d711c3fe))


### Bug Fixes

* **extractors:** attribute a module-level or class-body call to its owner ([#416](https://github.com/zaebee/codegraph-brain/issues/416)) ([#428](https://github.com/zaebee/codegraph-brain/issues/428)) ([c9f39b8](https://github.com/zaebee/codegraph-brain/commit/c9f39b81bf3a644441b7b5a3215df2d6b39b9489))
* **resolver:** an import prefix that reaches a node is first-party, not external ([#424](https://github.com/zaebee/codegraph-brain/issues/424)) ([#426](https://github.com/zaebee/codegraph-brain/issues/426)) ([50f8b7c](https://github.com/zaebee/codegraph-brain/commit/50f8b7c03d716c25f9341a879c76a8707f3853fb))
* **resolver:** resolve a name through the package that re-exports it ([#417](https://github.com/zaebee/codegraph-brain/issues/417)) ([#427](https://github.com/zaebee/codegraph-brain/issues/427)) ([ef76f95](https://github.com/zaebee/codegraph-brain/commit/ef76f9546b4477d4736406eabe417e6e49e68e23))


### Documentation

* auto-sync architecture graph and MCP reference ([#433](https://github.com/zaebee/codegraph-brain/issues/433)) ([40af3ee](https://github.com/zaebee/codegraph-brain/commit/40af3ee6839b53a953124e25f1f39b5379bff890))

## [0.17.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.16.0...codegraph-brain-v0.17.0) (2026-09-05)


### Features

* **extractors:** record what annotations say — self_types and REFERENCES edges ([#414](https://github.com/zaebee/codegraph-brain/issues/414), [#415](https://github.com/zaebee/codegraph-brain/issues/415)) ([#418](https://github.com/zaebee/codegraph-brain/issues/418)) ([eeaa60b](https://github.com/zaebee/codegraph-brain/commit/eeaa60b831956bb85fff88c987789ea179c2dc71))


### Bug Fixes

* **render:** draw one arrow per distinct triple in a mermaid diagram ([#421](https://github.com/zaebee/codegraph-brain/issues/421)) ([19a6067](https://github.com/zaebee/codegraph-brain/commit/19a6067f7c98d84f9d0a3e02e132f85ba0791817))


### Documentation

* auto-sync architecture graph after the annotation-edge work ([#420](https://github.com/zaebee/codegraph-brain/issues/420)) ([061843d](https://github.com/zaebee/codegraph-brain/commit/061843dd0ba997d2fd7549acaa72e7290b10f9cf))

## [0.16.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.15.0...codegraph-brain-v0.16.0) (2026-08-17)


### Features

* **bench:** a control arm, so an evidence result can be attributed ([#401](https://github.com/zaebee/codegraph-brain/issues/401)) ([#406](https://github.com/zaebee/codegraph-brain/issues/406)) ([241dbae](https://github.com/zaebee/codegraph-brain/commit/241dbae334be897cb6bf3d151d097b64ab5a56e7))
* **bench:** both skeptics over the same frozen passes, and the evidence bench replay dropped ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#411](https://github.com/zaebee/codegraph-brain/issues/411)) ([2c1d50b](https://github.com/zaebee/codegraph-brain/commit/2c1d50b2abb18b0c0bad1a9b46f71531eebba16c))
* **ci:** a floor under the size of the test suite itself ([#405](https://github.com/zaebee/codegraph-brain/issues/405)) ([#409](https://github.com/zaebee/codegraph-brain/issues/409)) ([0fbcbac](https://github.com/zaebee/codegraph-brain/commit/0fbcbac573651591750f017e3793b692c05377ce))
* **guardian:** tell the skeptic when no checker ran, and which verdict that takes ([#407](https://github.com/zaebee/codegraph-brain/issues/407)) ([#410](https://github.com/zaebee/codegraph-brain/issues/410)) ([2470ddb](https://github.com/zaebee/codegraph-brain/commit/2470ddb45a29f19e2abf90d2f6ce5b5fadad09f3))

## [0.15.0](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.14.1...codegraph-brain-v0.15.0) (2026-08-17)


### Features

* **bench:** rebuild frozen finder passes from the corpus, free ([#246](https://github.com/zaebee/codegraph-brain/issues/246)) ([#399](https://github.com/zaebee/codegraph-brain/issues/399)) ([b73f6e0](https://github.com/zaebee/codegraph-brain/commit/b73f6e0d40c5bdc4dadca4acd468d7835670be28))
* **guardian:** a replay arm that pairs each finding against its own baseline ([#401](https://github.com/zaebee/codegraph-brain/issues/401)) ([#404](https://github.com/zaebee/codegraph-brain/issues/404)) ([b2ab59a](https://github.com/zaebee/codegraph-brain/commit/b2ab59a8d7b5f62a307874b8a0eb9573273fa0ea))
* **guardian:** let the skeptic see what the checkers say ([#401](https://github.com/zaebee/codegraph-brain/issues/401)) ([#402](https://github.com/zaebee/codegraph-brain/issues/402)) ([af82147](https://github.com/zaebee/codegraph-brain/commit/af821473080bd3a717679e36c90164a43ca07f7b))

## [0.14.1](https://github.com/zaebee/codegraph-brain/compare/codegraph-brain-v0.14.0...codegraph-brain-v0.14.1) (2026-08-16)


### Bug Fixes

* **bench:** cutting a release turned the new reachability tests red ([#397](https://github.com/zaebee/codegraph-brain/issues/397)) ([efbca37](https://github.com/zaebee/codegraph-brain/commit/efbca37c13e452fdf7266619b36d86b0e11bb795))

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
