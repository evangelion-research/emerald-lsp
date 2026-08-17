# Emerald LSP

A language server for the Emerald programming language. This repository is
currently **design notes, not code** — the working document is
[`DESIGN.md`](DESIGN.md), which lays out what it takes to get from `emeraldc`
(7,000+ lines of C11: `lexer → parser → module link → check → codegen`, plus a
mark-and-sweep runtime) to a working language server.

The headline of the design: **most of the work is in the compiler, not the LSP
server.** The protocol layer is a few hundred lines of glue; the analysis
capabilities the protocol needs — error recovery, source ranges, a type
side-table, a symbol table — do not exist yet and all live in the compiler.

## Status

- **Phase:** design. The compiler-surgery list in `DESIGN.md` §1 (error
  recovery, end positions, allocation, structured output) blocks everything.
- **Planned shape:** Python + `pygls` for the protocol layer, C for analysis.
  `emeraldc` is extended into a JSON query backend and the server shells out to
  it per document version, with fresh-process analysis that sidesteps the
  compiler's memory model entirely.
- **Modules changed the architecture.** Emerald grew a module system
  (`src/module.c` in the compiler), which makes analysis per-program rather
  than per-file and adds a loader overlay for unsaved editor buffers. See
  `DESIGN.md` §2.

## pme — the package manager

`pme` — the package manager for Emerald, in design as a Python driver over the
same `-I` seam — is designed alongside this server. The two share one rule:

> **pme resolves, the LSP consumes.** The server reads `emerald.lock` and
> applies pme's frozen `-I` rule (each locked package's `src/` under
> `~/.emerald/store/`, dependencies before dependents). It never reimplements
> resolution — no version solving, no registry reads, no fetches.

`emerald.toml` and `emerald.lock` are analysis inputs: their changes are
watched and invalidate the analysis cache. pme's full implementation plan
(milestones 1–6, from manifest/lockfile/semver through a Stage-1 registry)
lives in the appendix of `DESIGN.md`.

## Related

- **Emerald** — the compiler (`emeraldc`), its module system, and runtime.
- **pme spec** — `evangelion-research/pme`'s `DESIGN.md`; authoritative for
  pme, expanded into an implementation plan in this repo's `DESIGN.md`
  appendix.
- **Design document** — [`DESIGN.md`](DESIGN.md): architecture, the feature
  ladder, testing strategy, and the suggested implementation order.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Evangelion Research.
