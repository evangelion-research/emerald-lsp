# Building the Emerald LSP — process notes

Notes on what it takes to get from `emeraldc` (5,981 lines of C11:
`lexer → parser → check → codegen`) to a working language server.

The headline: **most of the work is in the compiler, not the LSP server.**
The protocol layer is a few hundred lines of glue. The analysis capabilities
the protocol needs — error recovery, source ranges, a type side-table, a
symbol table — do not exist yet and all live in `../emerald/src`.

---

## 0. Language choice: Python for the server

**Decision: Python + `pygls` for the protocol layer, C stays for analysis.**

The split matters. There are two jobs here and they have opposite
requirements:

| Job | Wants | Language |
|---|---|---|
| Analysis (parse, typecheck, resolve names) | speed, direct AST access, the existing 6k lines of checker | **C** — already written |
| Protocol (JSON-RPC, document sync, position math, debounce) | JSON, string handling, async, fast iteration | **Python** |

Writing the protocol layer in C means hand-rolling a JSON parser, an event
loop, UTF-16 offset arithmetic, and a document store — several hundred lines
of fiddly code that has nothing to do with Emerald, plus it forces the arena
allocator problem (§1) on day one. That is the "overkill and stressful" path
and it buys nothing: the protocol layer is not the hot path. A keystroke
budget is ~100ms; JSON-RPC decode is sub-millisecond in any language.

### Why `pygls` specifically

`pygls` (with `lsprotocol` for the typed message definitions) gives you for
free the parts that are tedious and easy to get subtly wrong:

- `Content-Length` framing over stdio
- the `initialize` / `initialized` / `shutdown` / `exit` lifecycle and
  capability negotiation
- an in-memory document store fed by `didOpen` / `didChange` / `didClose`,
  including incremental sync if you enable it
- typed request/response models, so a malformed `Hover` is a type error
  rather than a client that silently ignores you
- request cancellation plumbing

What's left for you is a set of decorated handler functions:

```python
@server.feature(TEXT_DOCUMENT_HOVER)
def hover(ls, params: HoverParams) -> Hover | None:
    doc = ls.workspace.get_text_document(params.text_document.uri)
    result = analyze(doc.source, hover_at=params.position)
    ...
```

### Costs of Python, honestly

1. **Distribution.** A Go or Rust server ships as one static binary; a
   Python server needs an interpreter and dependencies. Mitigations, in
   order of preference:
   - publish to PyPI, tell users `uv tool install emerald-lsp` (or `pipx`);
     one command, isolated env, works everywhere
   - for the VS Code extension, bundle a venv or use PyInstaller so users
     install nothing
   - this is a real cost but a one-time packaging problem, not an ongoing
     tax on development
2. **Startup latency.** ~50–150ms for the interpreter plus imports. Paid
   once when the editor launches the server, then never again. Irrelevant.
3. **Per-request latency.** Only matters if you do analysis *in* Python,
   which you are not. See §2 — Python shells out or FFIs into C.
4. **GIL / concurrency.** Not a factor at this scale. `pygls` runs an
   asyncio loop; analysis happens in a subprocess, so it doesn't block.

### The alternative, if you change your mind later

Go (`sourcegraph/jsonrpc2`) is the other sane choice, purely for the
single-binary distribution story. The architecture below is unchanged
either way — the C side exposes JSON, and whoever consumes it is
interchangeable. Don't pick Go now for a distribution problem you don't
have yet. Ship in Python; port the ~400 lines of glue later if packaging
ever becomes the thing users complain about.

---

## 1. Make the compiler survivable as a library

`emeraldc` is a batch tool that assumes "one file, one process, then die."
Four assumptions break under an editor.

### 1a. The parser exits on the first syntax error

`src/parser.c:33` — `perror_at()` renders the diagnostic and calls `exit(1)`.
An editor buffer is syntactically broken most of the time you are typing, so
this is fatal, not cosmetic. Needed:

- **Error recovery.** On an unexpected token, record the diag and *panic-sync*
  to the next statement boundary — `}`, a newline at statement start, or one
  of `def`, `type`, `if`, `while`, `for`, `return` — then keep parsing.
- **An `S_ERROR` statement kind** (and `E_ERROR` expression kind) so the AST
  stays well-shaped even where the text is garbage. Completion inside a
  half-typed expression depends on there being *a* node at the cursor.
- `parse_program()` returns a partial `Program*` plus diags, and never exits.

Also `advance()` at `src/parser.c:45` bails on `TK_ERROR` from the lexer —
the lexer needs to produce a token and continue too (unterminated strings are
constant while typing).

### 1b. No end positions

`Expr`, `Stmt`, and `TypeExpr` all carry `line`/`col` only (`include/ast.h`).
Every LSP feature wants a **range**, not a point. Add `end_line`/`end_col` to
all three node structs, populated from the token span — `Token` already has
`len`, so the lexer side costs nothing.

This is a wide, mechanical diff. **Do it before writing any server code**,
because everything downstream assumes ranges exist.

### 1c. Everything leaks by design

`include/ast.h`: "Nodes are malloc'd and never freed." Fine for a process,
fatal for a server open for eight hours across 500 keystrokes. Two options:

- **Arena per document** — one bump allocator threaded through parser and
  checker, `arena_free()` on reparse. Cleanest, and speeds up parsing.
- **Process per request** — don't fix it at all; let the OS reclaim. Given
  the file sizes here this is likely <10ms per query and sidesteps the whole
  problem. See §2.

The process-per-request option pairs naturally with the Python decision and
is the recommended starting point.

### 1d. Output goes to stderr, and OOM calls `exit`

`exit(1)` on allocation failure in ~12 places (`check.c:74`, `diag.c:16`, …)
is acceptable. But the parser and checker also assume stderr is the reporting
channel; analysis modes must write structured output to stdout and keep
stderr for genuine crashes.

---

## 2. Architecture: C analyzes, Python speaks

The checker is the valuable, irreplaceable part — structural typing, unions,
flow narrowing, generics, `never`-based exhaustiveness. None of that gets
reimplemented in Python. So:

**Extend `emeraldc` into a query backend that emits JSON; Python consumes it.**

New modes alongside the existing `--emit-*` flags, which this fits cleanly:

```
emeraldc --lsp-diagnostics f.rald      # ~ --check --json, plus end positions
emeraldc --lsp-symbols     f.rald      # document symbols from the AST
emeraldc --lsp-hover       f.rald:12:7 # type + declaration at a position
emeraldc --lsp-index       f.rald      # everything: defs, uses, types, ranges
```

Add a `--stdin` / `-` input path: the editor buffer, not the file on disk, is
the source of truth, so Python pipes unsaved source in.

### Transport: subprocess first

Start with `subprocess.run(["emeraldc", "--lsp-index", "-"], input=src)`.

- fresh process per query means the leak in §1c never matters
- crashes are contained — a checker segfault on weird input returns a
  non-zero exit code instead of taking the server down
- trivially debuggable: every query is a shell command you can paste
- golden-testable exactly like the existing stage tests

Prefer **one fat `--lsp-index` call per document version**, cached in Python,
over one subprocess per hover. Compute the whole index on change (debounced),
then answer hover/definition/completion from the cached structure in memory.
That is one process spawn per ~300ms of typing, not one per cursor move.

If profiling ever shows the spawn cost mattering, the escape hatch is to
build `libemerald.a` and call it via `ctypes`/`cffi` — the JSON contract
stays identical, so it's a swap of one function, not a rewrite. Do not do
this preemptively.

---

## 3. What the compiler must expose

The real work, mostly new code in `check.c`.

### 3a. A position index

Given `(line, col)`, find the innermost AST node containing it. A sorted flat
array of `{start, end, node*}` built by a post-parse walk; binary search,
innermost wins. Requires §1b.

### 3b. An expression → type side table

`infer()` (`check.c:870`) computes a `Type*` for every expression and throws
it away. Record it: `Expr* → Type*`. That one change gives hover, and is the
input to field completion. `type_write()` already renders types to strings.

### 3c. A symbol table with locations

The checker has `Var`, `Alias`, and a function table, but tracks no
definition sites and no use sites. Add `def_line`/`def_col`/`def_end` to each,
and append to a `references[]` list on every name resolution.

That single table powers goto-definition, find-references, rename, document
symbols, and workspace symbols.

### 3d. Richer diagnostic JSON

Current JSON has `kind`, `code`, `line`, `column`, `message`, `expected`,
`actual`, `source_line`. Add:

- `end_line` / `end_col` — LSP wants a range to underline
- `severity` — `SEV_WARNING` exists in the enum but never reaches the JSON
- optionally `related: [{file, line, col, message}]` for "defined here" notes

### Not needed: cross-file resolution

Emerald has no import/module system, so there is no dependency graph to
maintain, no cross-file invalidation, and no workspace-wide reanalysis. That
is a large amount of work skipped. Worth remembering before adding imports —
they are much more expensive once a language server exists.

---

## 4. The Python layer

With `pygls` doing framing, lifecycle, and the document store, what's left:

- **Analysis cache**: `{uri: (version, index)}`. Recompute on `didChange`,
  serve everything else from cache.
- **Debounce**: ~150–300ms idle before spawning the checker; cancel/discard
  in-flight work when a newer document version lands. `asyncio` task
  cancellation, roughly 20 lines.
- **Position encoding** — *the trap*. LSP positions are **0-based lines** and
  **UTF-16 code units**. `emeraldc` uses **1-based lines** and **byte
  columns**. Any non-ASCII character in a string literal silently shifts
  every subsequent range on that line. Build one conversion module, test it
  with emoji and accented characters, use it at every boundary and nowhere
  else. LSP 3.17 allows negotiating `positionEncoding: "utf-8"` — do that
  when the client supports it, but keep the converter for those that don't.
- **Mapping**: compiler JSON → `lsprotocol` types. Mechanical.

---

## 5. Feature ladder

Ordered by value per unit of work.

1. **Diagnostics** — ~90% done. Needs §1a error recovery and §1b end
   positions. Ship alone as v0.1; it is most of the perceived value of any
   language server.
2. **Semantic tokens** — nearly free. The lexer already emits
   `{kind, line, col, len}`; map `TokKind` → LSP token types. Better
   highlighting than any TextMate grammar, and it needs no type information,
   so it works on broken buffers.
3. **Document symbols + folding ranges** — a walk over `S_FUNC`, `S_TYPEDEF`,
   top-level `S_ASSIGN`. Gives the outline view and breadcrumbs.
4. **Hover** — position index (§3a) + type side table (§3b). Rendered type
   plus the declaration site.
5. **Goto definition** — the symbol table (§3c).
6. **Completion** — keywords and in-scope names first. Then the good one:
   after `.`, resolve the object expression's type and list its record
   fields. Structural typing makes this genuinely useful rather than
   decorative.
7. **Find references / rename** — the use list; rename is references plus a
   `WorkspaceEdit`.
8. **Inlay hints** — inferred types on unannotated bindings. Cheap once the
   type table exists, and the best available showcase of the checker.
9. **Formatting** — needs a real pretty-printer over the AST
   (`ast_print_program` emits s-expressions, not source) *and* comment
   retention, which the AST currently discards entirely. A separate project.
   Do it last.

---

## 6. Editor integration

- **VS Code**: `package.json` contributing the `.rald` language,
  `language-configuration.json` (line comment `#`, brackets, auto-closing
  pairs), and ~30 lines of client code launching the server. Bundle the
  Python env so users install nothing.
- **Neovim / Helix / Zed**: no extension needed, just config pointing at the
  server binary. Ship copy-pasteable snippets in the README.
- A minimal TextMate grammar is still worth having as the fallback before
  the server attaches, and for GitHub syntax highlighting.

---

## 7. Testing

Extend the existing golden-test culture rather than inventing something new.

- **Analysis goldens**: `--lsp-index` output diffed against checked-in JSON,
  in `tests/lsp/` beside the four existing stage suites. This is where most
  of the real coverage lives, and it needs no LSP client at all.
- **Session tests**: pipe a scripted JSON-RPC conversation into the server
  and golden-diff the responses. `pygls` ships test helpers for driving a
  server in-process, which is easier than framing messages by hand.
- **Truncation fuzz**: take each `examples/*.rald`, cut at every byte offset,
  assert the parser terminates without crashing and produces *some* AST.
  This one test catches the majority of error-recovery bugs, and it is ~15
  lines of Python.
- **Position-encoding tests**: a file with emoji and combining characters,
  asserting that ranges land on the right characters in every editor.

---

## Suggested order

```
end positions in AST  →  parser error recovery  →  --lsp-index JSON
      →  pygls skeleton + sync + diagnostics  →  semantic tokens
      →  type side table  →  hover  →  symbol table  →  goto-def
      →  completion  →  references / rename  →  inlay hints
```

The first two steps are unglamorous compiler surgery with nothing to demo,
and it is tempting to skip ahead to the JSON-RPC loop where progress is
visible. Don't. Every feature past diagnostics is built on source ranges and
on parsing broken text; retrofitting either one later means touching every
handler a second time.
