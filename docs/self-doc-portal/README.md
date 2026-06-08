# Self‑Documenting Knowledge Portal

This documentation is **self‑generated** from the CodeGraph Intelligence System (CGIS). The graph of the codebase is automatically injected below.

<!-- START_CGIS_GRAPH -->
```mermaid

### Execution flow for `src.cgis.pipeline.IngestionPipeline.run`:

```mermaid

graph TD
classDef classNode fill:#e8f5e9,stroke:#2e7d32,stroke-width:1.5px,color:#1b5e20;
classDef funcNode fill:#e3f2fd,stroke:#1565c0,stroke-width:1.5px,color:#0d47a1;
classDef methodNode fill:#f3e5f5,stroke:#7b1fa2,stroke-width:1.5px,color:#4a148c;
classDef unresolvedNode fill:#fffde7,stroke:#fbc02d,stroke-width:1.5px,stroke-dasharray: 4 4,color:#f57f17;
classDef defaultNode fill:#fafafa,stroke:#9e9e9e,stroke-width:1.5px,color:#212121;
classDef stdlibNode fill:#eceff1,stroke:#607d8b,stroke-width:1px,color:#455a64;
classDef externalNode fill:#fff3e0,stroke:#e65100,stroke-width:1px,stroke-dasharray: 3 3,color:#bf360c;

    n_12b2950a4cfa8fe1fbea7b9e152440fe["FileNotFoundError (EXTERNAL:0)"]:::stdlibNode
    n_a980cd069ac6c3cbdc760505f56bb34d["NotADirectoryError (EXTERNAL:0)"]:::stdlibNode
    n_6727ce9e58270b9f2074a9eab8f93250["extend (EXTERNAL:0)"]:::unresolvedNode
    n_d1fc5dbed6c8a3a22520222e971bdcd4["startswith (EXTERNAL:0)"]:::unresolvedNode
    n_c30f6eb44f39f9da29b42cfac3f268a1["model_copy (EXTERNAL:0)"]:::unresolvedNode
    n_cf44efc26aa485f76e599e6cc409fa2a["removeprefix (EXTERNAL:0)"]:::unresolvedNode
    n_bf1c0aa33b812062ba4ad13f30977a88["startswith (EXTERNAL:0)"]:::unresolvedNode
    n_715cd8537564fcca01c52046b9a1c7f4["setdefault (EXTERNAL:0)"]:::unresolvedNode
    n_ca272b4094f0d4c543e660efe4b6de2c["append (EXTERNAL:0)"]:::unresolvedNode
    n_f31899f73b912f2528af744f8e873783["read (EXTERNAL:0)"]:::unresolvedNode
    n_d89f566ed27381a15fa69599a71d808f["resolve (EXTERNAL:0)"]:::unresolvedNode
    n_793602394217ca88d4d99618685afbfa["relative_to (EXTERNAL:0)"]:::unresolvedNode
    n_d288654ec3463b3d82a43dd6d7b332ac["as_posix (EXTERNAL:0)"]:::unresolvedNode
    n_f5a8e923f8cd24b56b3bab32358cc58a["len (EXTERNAL:0)"]:::stdlibNode
    n_10ae9fc7d453b0dd525d0edf2ede7961["list (EXTERNAL:0)"]:::stdlibNode
    n_11ec4b545532e4cb1c1e38d7faf862dd["exception (EXTERNAL:0)"]:::unresolvedNode
    n_c20dc361806528e2947ff32b84eb31ef["info (EXTERNAL:0)"]:::unresolvedNode
    n_842601fd34b82d3cc06866ae1d478ec6["warning (EXTERNAL:0)"]:::unresolvedNode
    n_d8bd79cc131920d5de426f914d17405a["min (EXTERNAL:0)"]:::stdlibNode
    n_44b302355348b48456c038aa54f7c936["setdefault (EXTERNAL:0)"]:::unresolvedNode
    n_decf2cb5d7c3837b06a484cc2a067f4e["append (EXTERNAL:0)"]:::unresolvedNode
    n_f5cfce7a410115686b43fa841cd0d8ea["Path (EXTERNAL:0)"]:::stdlibNode
    n_6cb15e2ad2bdb6888b3cdb49670a3619["exists (EXTERNAL:0)"]:::stdlibNode
    n_ae91ad9c9f935217f40ea34a14d40e51["is_dir (EXTERNAL:0)"]:::stdlibNode
    n_7f8d03ee06616ea901605fea8ff702d3["open (EXTERNAL:0)"]:::stdlibNode
    n_72411b5fdbc5a65f5b1d2691abcc84a9["resolve (EXTERNAL:0)"]:::stdlibNode
    n_cac6fd0a94704c000856dacd1beb890c["add_task (EXTERNAL:0)"]:::unresolvedNode
    n_35f6a1141a8d4232d498d310c2933291["update (EXTERNAL:0)"]:::unresolvedNode
    n_b9fb47f890fa51bda21e7466cb0cca5a["removeprefix (EXTERNAL:0)"]:::unresolvedNode
    n_7883f4e3032b1bd147b905080e183249["startswith (EXTERNAL:0)"]:::unresolvedNode
    n_8aa0a4bf8c5af91058f4b354a3ad43ff["append (EXTERNAL:0)"]:::unresolvedNode
    n_2fe5d003595d6c07ca93b2d7ce2f702b["Console (EXTERNAL:0)"]:::externalNode
    n_d3257bceb49cb38fc8b7b113ec6cf667["Progress (EXTERNAL:0)"]:::externalNode
    n_e0d143d6b2ad048b8384ebe9dfb35a05["SpinnerColumn (EXTERNAL:0)"]:::externalNode
    n_11419227a3cf58d5d56a9ea17743ce92["TextColumn (EXTERNAL:0)"]:::externalNode
    n_d4b1de0b87b858dd2cc07a3ffcafdb8d["items (EXTERNAL:0)"]:::unresolvedNode
    n_cdaeeeba9b4a4c5ebf042c0215a7bb0e["set (EXTERNAL:0)"]:::stdlibNode
    n_e6fa672211cba473e52ebfd73470e0b9["get (EXTERNAL:0)"]:::unresolvedNode
    n_cea494f04dc2c4c57d2c44eb5e8ef03a["parse (base.py:17)"]:::methodNode
    n_143f8a1bd9b6fcf8cb45941648589fa2["_compute_hash (pipeline.py:33)"]:::methodNode
    n_619bdd762d56a2f5ab8e1774ed883286["_get_extractor (pipeline.py:196)"]:::methodNode
    n_75cd66340b7949529ef32dba25f68f54["_persist_incremental (pipeline.py:157)"]:::methodNode
    n_ff98a6d5d900a48bfd7082a84c3bc780["_process_file (pipeline.py:127)"]:::methodNode
    n_6e1c8aced159149485a2480454346f3b["run (pipeline.py:36)"]:::methodNode
    n_340edaa713cdbc2a02e4fdbce586d94e["extend (EXTERNAL:0)"]:::unresolvedNode
    n_02ad7efd497ce917c064434fdd019dad["add (EXTERNAL:0)"]:::unresolvedNode
    n_2f17b298f4cb12dc6673f0553375ccba["endswith (EXTERNAL:0)"]:::unresolvedNode
    n_45beaced19a244815f4bdf8ff2e86374["ResolverEngine (engine.py:14)"]:::classNode
    n_7bd489a27c236f7a85f8533bf64d8e34["__init__ (engine.py:20)"]:::methodNode
    n_315ebcd30e423e177b8e9f6b469c3165["_add_node_to_suffix_map (engine.py:108)"]:::methodNode
    n_4f56cfdf07649d9c35fb54699c754124["_build_external_roots (engine.py:95)"]:::methodNode
    n_b3e4f7acaf909fa7d35626f5873a0b26["_build_indices (engine.py:42)"]:::methodNode
    n_8a9cd58230a4867e7d862be6b9bf00ce["_build_inheritance_tree (engine.py:70)"]:::methodNode
    n_506a5c2ec6c1d8af64565aec3955e8ae["_classify_fqn (engine.py:118)"]:::methodNode
    n_689b5c8405002c4da6e19c084776daeb["_ensure_virtual_node (engine.py:195)"]:::methodNode
    n_d3e4c21325fbff6c946303540d1388e8["_get_normalized_file_path (engine.py:281)"]:::methodNode
    n_f2c6c50440121b9acec2d6e8e248fd8d["_make_virtual_node (engine.py:135)"]:::methodNode
    n_c13051f40f08f35da521011a51c13020["_map_to_node_fqn (engine.py:228)"]:::methodNode
    n_888f7eeb6c5dc6ecdd4afabe56f9f427["_resolve_class_ref (engine.py:78)"]:::methodNode
    n_3e6c351a28f6d3b7fc84309dffb15950["_resolve_global_call (engine.py:288)"]:::methodNode
    n_8e1299da9892d5eeb0388c75093d1e66["_resolve_local_type_call (engine.py:269)"]:::methodNode
    n_f4d08ba21a97646afe0528e2fd085965["_resolve_method_on_class_hierarchy (engine.py:212)"]:::methodNode
    n_3e69acdfc0135afb3e6f2f09555a57a9["_resolve_self_call (engine.py:200)"]:::methodNode
    n_8f5aea8b527b0bd774ff04d20dfa9b63["_resolve_via_global_symbols (engine.py:307)"]:::methodNode
    n_560fff7aa8971d06b95e489cf19c08ab["_resolve_via_import_map (engine.py:253)"]:::methodNode
    n_9d3dedbd4338ae8b0e2a47be5eca1f39["resolve (engine.py:148)"]:::methodNode
    n_992bb66dd1ac75a7e7f41ff0d225af81["get_all_tracked_files (sqlite_store.py:414)"]:::methodNode
    n_b0c15c8fa4a8773706b2b4b8f6877c2f["get_file_hash (sqlite_store.py:246)"]:::methodNode
    n_0a2476269f0ece58331d619ca537dd41["get_nodes_by_file (sqlite_store.py:310)"]:::methodNode
    n_a3db15850aac1429536af178dd82aa04["save_incremental_batch (sqlite_store.py:280)"]:::methodNode
    n_047799b485368710583e89b503ae218d["upsert_nodes (sqlite_store.py:163)"]:::methodNode
    n_341be97d9aff90c9978347f66f945b77["str (EXTERNAL:0)"]:::stdlibNode
    n_4626476a9dc2d3de2610b43d70c37a7f["values (EXTERNAL:0)"]:::unresolvedNode
    n_5aa0fc3a62038f43c0b93b539bcddd95["walk (EXTERNAL:0)"]:::unresolvedNode
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_cdaeeeba9b4a4c5ebf042c0215a7bb0e
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_f5cfce7a410115686b43fa841cd0d8ea
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_6cb15e2ad2bdb6888b3cdb49670a3619
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_12b2950a4cfa8fe1fbea7b9e152440fe
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_ae91ad9c9f935217f40ea34a14d40e51
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_a980cd069ac6c3cbdc760505f56bb34d
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_72411b5fdbc5a65f5b1d2691abcc84a9
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_d3257bceb49cb38fc8b7b113ec6cf667
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_e0d143d6b2ad048b8384ebe9dfb35a05
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_11419227a3cf58d5d56a9ea17743ce92
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_2fe5d003595d6c07ca93b2d7ce2f702b
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_cac6fd0a94704c000856dacd1beb890c
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_5aa0fc3a62038f43c0b93b539bcddd95
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_d1fc5dbed6c8a3a22520222e971bdcd4
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_619bdd762d56a2f5ab8e1774ed883286
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_d288654ec3463b3d82a43dd6d7b332ac
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_793602394217ca88d4d99618685afbfa
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_d89f566ed27381a15fa69599a71d808f
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_842601fd34b82d3cc06866ae1d478ec6
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_341be97d9aff90c9978347f66f945b77
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_02ad7efd497ce917c064434fdd019dad
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_ff98a6d5d900a48bfd7082a84c3bc780
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_35f6a1141a8d4232d498d310c2933291
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_cac6fd0a94704c000856dacd1beb890c
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_c20dc361806528e2947ff32b84eb31ef
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_45beaced19a244815f4bdf8ff2e86374
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_9d3dedbd4338ae8b0e2a47be5eca1f39
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_6727ce9e58270b9f2074a9eab8f93250
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_35f6a1141a8d4232d498d310c2933291
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_c20dc361806528e2947ff32b84eb31ef
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_f5a8e923f8cd24b56b3bab32358cc58a
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_f5a8e923f8cd24b56b3bab32358cc58a
    n_6e1c8aced159149485a2480454346f3b -->|CALLS| n_75cd66340b7949529ef32dba25f68f54
    n_619bdd762d56a2f5ab8e1774ed883286 -->|CALLS| n_d4b1de0b87b858dd2cc07a3ffcafdb8d
    n_619bdd762d56a2f5ab8e1774ed883286 -->|CALLS| n_2f17b298f4cb12dc6673f0553375ccba
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_decf2cb5d7c3837b06a484cc2a067f4e
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_44b302355348b48456c038aa54f7c936
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_e6fa672211cba473e52ebfd73470e0b9
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_ca272b4094f0d4c543e660efe4b6de2c
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_715cd8537564fcca01c52046b9a1c7f4
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_992bb66dd1ac75a7e7f41ff0d225af81
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_a3db15850aac1429536af178dd82aa04
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_047799b485368710583e89b503ae218d
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_c20dc361806528e2947ff32b84eb31ef
    n_75cd66340b7949529ef32dba25f68f54 -->|CALLS| n_c20dc361806528e2947ff32b84eb31ef
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_7f8d03ee06616ea901605fea8ff702d3
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_f31899f73b912f2528af744f8e873783
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_143f8a1bd9b6fcf8cb45941648589fa2
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_b0c15c8fa4a8773706b2b4b8f6877c2f
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_340edaa713cdbc2a02e4fdbce586d94e
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_0a2476269f0ece58331d619ca537dd41
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_cea494f04dc2c4c57d2c44eb5e8ef03a
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_c20dc361806528e2947ff32b84eb31ef
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_f5a8e923f8cd24b56b3bab32358cc58a
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_340edaa713cdbc2a02e4fdbce586d94e
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_340edaa713cdbc2a02e4fdbce586d94e
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_11ec4b545532e4cb1c1e38d7faf862dd
    n_ff98a6d5d900a48bfd7082a84c3bc780 -->|CALLS| n_341be97d9aff90c9978347f66f945b77
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_7bd489a27c236f7a85f8533bf64d8e34
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_b3e4f7acaf909fa7d35626f5873a0b26
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_8a9cd58230a4867e7d862be6b9bf00ce
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_888f7eeb6c5dc6ecdd4afabe56f9f427
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_4f56cfdf07649d9c35fb54699c754124
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_315ebcd30e423e177b8e9f6b469c3165
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_506a5c2ec6c1d8af64565aec3955e8ae
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_f2c6c50440121b9acec2d6e8e248fd8d
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_9d3dedbd4338ae8b0e2a47be5eca1f39
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_689b5c8405002c4da6e19c084daeb
       n_bebeaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_3e69acdfc0135afb3e6f2f09555a57a9
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_f4d08ba21a97646afe0528e2fd085965
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_c13051f40f08f35da521011a51c13020
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_560fff7aa8971d06b95e489cf19c08ab
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_8e1299da9892d5eeb0388c75093d1e66
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_d3e4c21325fbff6c946303540d1388e8
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_3e6c351a28f6d3b7fc84309dffb15950
    n_45beaced19a244815f4bdf8ff2e86374 -->|DECLARES| n_8f5aea8b527b0bd774ff04d20dfa9b63
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_bf1c0aa33b812062ba4ad13f30977a88
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_cf44efc26aa485f76e599e6cc409fa2a
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_888f7eeb6c5dc6ecdd4afabe56f9f427
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_8aa0a4bf8c5af91058f4b354a3ad43ff
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_c30f6eb44f39f9da29b42cfac3f268a1
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_689b5c8405002c4da6e19c084daeb
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_bf1c0aa33b812062ba4ad13f30977a88
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_8aa0a4bf8c5af91058f4b354a3ad43ff
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_689b5c8405002c4da6e19c084daeb
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_cf44efc26aa485f76e599e6cc409fa2a
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_7883f4e3032b1bd147b905080e183249
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_3e69acdfc0135afb3e6f2f09555a57a9
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_b9fb47f890fa51bda21e7466cb0cca5a
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_3e6c351a28f6d3b7fc84309dffb15950
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_d8bd79cc131920d5de426f914d17405a
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_8aa0a4bf8c5af91058f4b354a3ad43ff
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_c30f6eb44f39f9da29b42cfac3f268a1
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_689b5c8405002c4da6e19c084daeb
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_10ae9fc7d453b0dd525d0edf2ede7961
    n_9d3dedbd4338ae8b0e2a47be5eca1f39 -->|CALLS| n_4626476a9dc2d3de2610b43d70c37a7f
```

## Quickstart
```bash
# Ingest the source code into a graph database
cgis ingest ./src --output graph.db

# Show the call‑graph of the main pipeline
cgis trace "src.cgis.pipeline.IngestionPipeline.run"
```
