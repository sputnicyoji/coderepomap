# Changelog

All notable changes to `coderepomap` are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### TypeScript language plug-in

- New `coderepomap.typescript` subpackage registering
  `TypeScriptParser(LanguageParser)`; separate tree-sitter parsers for the
  TS and TSX dialects, selected per file extension.
- AST walker covering: per-file module symbols, per-directory `package`
  symbols (the renderer's widened-mode sentinel, mirroring Go), classes
  (incl. `abstract`), interfaces (+ `method_signature` members), enums
  (`class` kind with `lang_meta.declaration_form`), type aliases
  (`interface` kind), top-level functions AND arrow consts, class methods
  (accessibility / static / async in `lang_meta`), class property arrow
  methods, heritage clauses (`extends` → `inherits`, `implements` →
  `implements`), file-local import binding table, in-body call / new
  references with locals + builtins suppression.
- ESM-aware relative-import resolution: `./search.js` strips the compiled
  extension and finds `search.ts` on disk; extensionless directory imports
  resolve through `index.ts`; bare specifiers (npm / node builtins) stay
  unresolved and render under L3 External References (one ref per import
  statement, not per binding, to keep L3 lean).
- Named imports / re-exports predict `typescript:<module>.<Name>` ids;
  crosslang flips exact matches and a new Path 2b trailing-segment trim
  promotes misses (plain consts, type-only names, re-exported bindings) to
  the deepest existing symbol — usually the file module node.
- Stable id scheme documented in `core/identity.py`: `typescript:<rel-path>`
  modules (directory separators stay `/`, `.` reserved for symbol segments),
  `typescript:<dir>/` packages (trailing slash keeps `core/assembler.ts` and
  `core/assembler/` disjoint), `.{Type}.{method}` members.
- Regex fallback (when `tree-sitter-typescript` is missing) covering
  classes, interfaces, functions, enums, type aliases, arrow consts, and
  imports at module level.
- Default excludes for Vitest/Jest co-located tests, `node_modules`, build
  output, and `**/*.d.ts` (ambient declarations; dotted stems would also
  fight the trailing-segment trim).
- `repomap init --lang typescript` writes a TypeScript-flavored
  `.repomap/config.yaml` (root_path `src`, TS boost patterns + categories).
- `pyproject.toml`: new `[typescript]` extras (`tree-sitter>=0.21`,
  `tree-sitter-typescript>=0.21`); `all` extras updated;
  `coderepomap.typescript` package-data entry; `typescript` keyword.
- Tests: `tests/test_typescript_parser.py` (35 cases over a fixture project
  exercising all four import shapes, heritage, bodies, crosslang promotion,
  and the regex fallback), `tests/test_typescript_generate.py` (end-to-end
  pipeline incl. graph-edge assertions), CLI init + packaging tests.

#### Go language plug-in

- New `coderepomap.go` subpackage registering `GoParser(LanguageParser)`.
- Tree-sitter-go AST walker covering: package symbols, top-level functions,
  type declarations (`struct` → `class`, `interface` → `interface`), struct
  fields, methods (value + pointer receivers, generic receivers like
  `func (s *Service[T]) Run()`), struct + interface embedding (→ `inherits`
  references), file-local import alias table, cross-package call resolution,
  composite-literal `uses` references, generic type parameters captured in
  `lang_meta.generic_params`.
- Regex fallback (when `tree-sitter-go` is missing) covering package, types,
  functions, methods, and imports — incl. aliased / blank / dot single-line
  forms and comment-resilient import blocks.
- `go.mod`-aware module path resolution that walks ancestors and falls
  through go.mod files without a parseable `module` directive (so stray
  in-flight migration stubs don't short-circuit the walk).
- Stable id scheme `go:<module-path>/<rel-dir>.<Type>.<Method>` with
  module-path normalization (Windows separators → `/`, empty module_path
  fallback to rel-dir).
- Default boost patterns for Go conventions (`Service`, `Handler`, `Server`,
  `Client`, `Manager`, `Repository`, `Store`, `-er` interface suffix).
- Default categories: `Cmd`, `Internal`, `Pkg`, `API`, `Domain`, `Storage`.

#### Generated-code detection (Go)

- `default_excludes` includes `**/*.pb.go`, `**/*_gen.go`,
  `**/*.generated.go` (protoc-gen-go / stringer / mockgen / easyjson output).
- Content sentinel: parse_file scans the first 4 KB for the canonical
  `// Code generated ... DO NOT EDIT.` marker. On match, only the package
  symbol is emitted (with `lang_meta.generated=True`) so importers still
  resolve, but business symbols and references are skipped. Cuts symbol
  count by ~80% on typical Go service repos.

#### CLI / packaging

- `repomap init --lang go` writes a Go-flavored `.repomap/config.yaml`.
- `pyproject.toml`: new `[go]` extras (`tree-sitter>=0.21`,
  `tree-sitter-go>=0.21`); `all` extras updated; `coderepomap.go` package-
  data entry; `go` / `golang` keywords.
- Generalized unity-preset rejection (`preset == unity AND lang != csharp`).

#### Core seam (opt-in widening)

- `LanguageParser.graph_node_kinds: List[str] | None = None` class attribute.
  Languages with non-class entry symbols declare them here. Generator
  collects the union across active parsers and passes `node_kinds=` to
  `build_graph`. C# and Lua keep `None` so v0.1.0 byte-equivalence holds.
- `build_module_stats` now exposes `symbol_count` / `entries` alongside the
  legacy `class_count` / `classes`, plus a `widened: bool` per-module flag
  driven by the presence of `package`-kind symbols (Go's structural
  sentinel; C# and Lua never emit it). Files containing only non-entry-kind
  symbols (enums, methods alone, ...) no longer create module entries —
  matches v0.1.0 behavior for the C# baseline.
- `render_l1` / `render_l2` switch wording per-module: pure-class modules
  keep `(N classes)` / "Core Entry Classes" / "Entry Class" column header;
  modules with a `package` symbol render `(N entry symbols)` / "Core Entry
  Symbols" / "Entry Symbol" column header.
- `render_meta.top_modules` emits both `classes` and `entries` keys in
  widened runs (sorted by `symbol_count`); pure-class runs keep the v0.1.0
  schema (only `classes`).

#### Renderer budget filling

- L1 candidate pool widened: 10 → 30 modules per category, 20 → 100 ranked
  entries. Existing trim loop enforces the configured `tokens.l1_skeleton`
  cap; small projects see the same output, large projects fill the budget
  (X15_Server: L1 286 → 997 tokens / 1000 cap).
- L2 candidate pool widened: 15 → 50 modules, 5 → 20 entries per module.

### Fixed

Code-review findings on the Go PR (15 top-severity + 4 cosmetic):

- **Generic receiver methods** were silently dropped because receiver-type
  extraction matched only `type_identifier` and `pointer_type(type_identifier)`
  — methods on `Service[T]` now correctly emit via `_extract_named_type`
  helper that also handles `generic_type` and `pointer_type(generic_type)`.
- **Callback-parameter spurious refs**: `func Apply(cb func()) { cb() }`
  was emitting `cb()` as a same-package function reference because
  `locals_set` only tracked `short_var_declaration` identifiers.
  `_collect_parameter_names` now seeds locals_set from each function's /
  method's parameter list.
- **RecursionError on deep ASTs**: `_walk_node` was unbounded recursive
  traversal; a deeply-nested binary expression (~2000 terms) blew Python's
  recursionlimit and killed the whole generator run. Now wrapped in
  try/except inside `_walk_body` — the function symbol stays emitted; only
  in-body refs are skipped for the deep body, with a one-line warning.
- **Init failure latched the run to regex mode**: `_init_parser` was
  setting `_initialized=True` on both success and failure paths. A
  transient ImportError thus permanently downgraded the run. Now only
  success sets the flag (matches `LegacyCSharpParser` policy).
- **Stray go.mod short-circuited the walk**: `_resolve_module_path` broke
  out of the ancestor walk on the FIRST go.mod found, even when that
  go.mod lacked a parseable `module` directive (migration stub, commented
  module line). Walk now falls through to ancestors.
- **`_import_tables` dead code** removed — written per-file but never
  read; comment was stale ("Persist for Task 14's call-expression walker"
  but call resolution already used the local `imports` parameter).
- **Regex fallback gaps** (when `tree-sitter-go` is missing): aliased /
  blank / dot single-line imports were dropped, all Symbols were missing
  `signature=` producing blank L2 headers, and a `)` inside a `//` comment
  in an import block truncated the match. All fixed.
- **render_l2 leaked C# interfaces** in multi-lang `langs: [csharp, go]`
  runs because the entry-kind filter widened unconditionally. Now gated
  per-module by the `package`-kind sentinel.
- **L1 wording flipped C# modules to "entry symbols"** in any multi-lang
  run with Go because `widened_mode` was global. Now per-module via the
  `info["widened"]` flag in `build_module_stats`.
- **L1 column header** was hard-coded `Entry Class`; now flips to
  `Entry Symbol` when any module is widened, matching the section header.
- **L2 module header** `(N classes, rank: ...)` was unconditional; now
  flips per-module to `(N entry symbols, rank: ...)`.
- **top_modules sorted by class_count** demoted Go modules with no
  structs. Now sorts by `symbol_count` when any module is widened and
  emits both `classes` + `entries`.
- **node_kinds_union force-injected `class`** even when a parser declared
  `graph_node_kinds = ["function"]` (or similar) intentionally excluding
  class. Now class is added only when at least one ACTIVE language did not
  declare `graph_node_kinds` (preserving C# / Lua co-language visibility
  alongside Go).
- **`build_module_stats` created module entries for non-entry symbols**
  (enums, methods alone) — broke the C# baseline byte-equivalence by
  rendering `- Enums/ (0 classes)` lines. Now skips non-entry-kind symbols.
- **CLI docstring** still listed `--lang csharp|lua`; updated to include
  `go`.
- **Windows-separator test** used invalid escape `"pkg\service"` (Python
  3.12 SyntaxWarning, future SyntaxError). Changed to raw string
  `r"pkg\service"`.

### Tests

- 206 tests total (was 135 before this work): Go parser, end-to-end Go
  generator pipeline, Go source-discovery scenarios, CLI init + pyproject
  smoke, `LanguageParser.graph_node_kinds` ABC contract, build_graph
  node_kinds union, renderer wording / module stats / per-module widened
  mode, Go id helpers, plus regression cases for every code-review
  finding.
- C# baseline (`tests/test_csharp_parser_baseline.py`,
  `tests/test_generator_baseline.py`) remains byte-equivalent across all
  changes (11/11 green) — the seam additions are structurally additive.

### Real-world validation

Tested on `git.tap4fun.com/x15/server` (7144 Go files, no prior repomap):

| Metric | Before fixes | After fixes |
|---|---|---|
| Parse time | 32.5 min | 10.4 min |
| Symbols | 97849 | 20539 (−79%) |
| References | 86227 | 37131 |
| L1 tokens / cap | 286 / 1000 | 997 / 1000 |
| L2 tokens / cap | 278 / 2000 | 803 / 2000 |
| L3 tokens / cap | 2992 / 3000 | 2996 / 3000 |

## [0.2.0]

Renamed from `csharp-repomap`. Multi-language plugin architecture
(C# + Lua), U3D cross-language references (Lua → C#), PageRank-ranked
layered output. See repository history for details — this is the first
version under the `coderepomap` name.

## [0.1.0]

Initial C#-only release as `csharp-repomap`.
