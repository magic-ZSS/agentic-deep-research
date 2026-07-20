# Third-Party Notices for Phase 0 References

This file records the third-party materials introduced or fixed by Phase 0. It is not a complete inventory of every transitive dependency already declared by the project.

## Optional dependency

- **DeepEval 4.1.1** — https://github.com/confident-ai/deepeval — Apache License 2.0. It is declared only in the optional `eval` extra. Production and offline smoke execution do not require or import it.

## Read-only development references

The following repositories are fixed as read-only development submodules. Referencing a repository does not mean its code is linked into the production package.

- Future-House/paper-qa at `d7675d7b7eddeb3535e8c260399c5bbeeb818c50` — Apache-2.0.
- confident-ai/deepeval at `58c9ef78a4634ba119c7d2cc145f5cf9aeb24524` — Apache-2.0.
- langchain-ai/langmem at `a2d580946465137c89162e67dc0b18108bd4850c` — MIT.
- langchain-ai/langgraph at `49ae27c2ae983cfb92091b0dea9f7bc37a716479` — MIT.
- modelcontextprotocol/servers at `d31124c982401739917fd817c2a59db344529c16` — contribution-level MIT to Apache-2.0 transition; non-specification documentation is CC-BY-4.0. File-level review is required before reuse.

Phase 0 copies no source code, test fixture, paper, or webpage content from these repositories. Future code copying must record the exact source file, commit, applicable license, local modifications, copyright notice, and any required NOTICE attribution.

Machine-readable URLs, commits, version evidence, license file paths, and hashes are stored in `doc/reference/refs.lock.json`.
