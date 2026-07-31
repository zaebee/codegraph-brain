# Privacy Policy

**Last updated:** 31 July 2026

This policy covers the **CGIS Claude Code plugin** and the **`codegraph-brain`** package it installs.

## The short version

CGIS collects nothing. There is no telemetry, no analytics, no usage reporting, no crash reporting, and no account. Your source code and the graph built from it stay on your machine. Nothing is sent to the author of this software, and there is no server to send it to.

## What runs where

**On your machine.** CGIS parses your source files with Tree-sitter and writes the resulting graph to a SQLite file (`graph.db` by default) in your project directory. Parsing, storage and every query happen locally. The graph contains structural information derived from your code — file paths, symbol names, line numbers and the call relationships between them — and it never leaves your disk.

**Over the network, once.** On first use the plugin fetches the `codegraph-brain` package from the Python Package Index using `uvx`. This is an ordinary package download. PyPI is operated by the Python Software Foundation and applies its own logging and privacy practices to that request; see the [PSF Privacy Notice](https://www.python.org/privacy/). After the download is cached, the plugin makes no further network requests.

## Data we collect

None.

We do not collect, transmit, store or have access to: your source code, the graphs built from it, file or repository names, prompts, tool calls, IP addresses, machine identifiers, usage counts, error reports, or anything else. This is not a matter of policy but of architecture — the software has no code that sends data anywhere, and no infrastructure to receive it.

## Guardian, and when data does leave your machine

The `codegraph-brain` package also contains **Guardian**, a code reviewer that sends diffs and graph context to a large language model. **Guardian is not part of the Claude Code plugin**: the plugin neither ships nor invokes it, and it does nothing unless you configure it and run it deliberately, normally in CI.

If you choose to use Guardian, you select the model provider yourself — Google Gemini, Mistral, Cohere, or a local Ollama instance — and supply your own credentials. In that case the code being reviewed is sent to whichever provider you chose, under **that provider's** privacy policy and terms, not this one. Choosing Ollama keeps it local. We never see that traffic and never receive a copy.

Credentials for those providers are read from your environment and used only to call the provider they belong to. They are not logged, stored or transmitted anywhere else.

## What CGIS never touches

CGIS reads source files under the path you point it at. It does not read your SSH keys, cloud credentials, browser data, keychain, clipboard, shell history, or files outside the directory you ingest. The plugin registers no Claude Code hooks, so it observes nothing about sessions, prompts or tool calls beyond its own tool invocations.

## Children

This is a developer tool and is not directed at children.

## Changes

Material changes to this policy will be recorded in this file, and its history is public in the repository's git log.

## Contact

Questions about privacy: open an issue at [github.com/zaebee/codegraph-brain/issues](https://github.com/zaebee/codegraph-brain/issues), or email zaebuntu@gmail.com.
