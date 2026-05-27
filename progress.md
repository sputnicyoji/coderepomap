# Progress

Mission: add Go language support to `coderepomap`. Single PR scope; no
cross-language analysis in Phase 1.

## Status

**Done.** 28 commits on `origin/main`. 206 / 206 tests green. C# baseline
byte-equivalent (11 / 11). Real-world validation on a 7000-file Go service
repo.

## Timeline

| Date | Phase | Outcome |
|---|---|---|
| 2026-05-26 | Brainstorming + spec | 4 decision points resolved (id namespace, `_test.go` default, interface boost, no cross-lang in Phase 1). Spec in `docs/superpowers/specs/2026-05-26-go-language-support-design.md`. |
| 2026-05-26 | Implementation plan | 25 TDD tasks in `docs/superpowers/plans/2026-05-26-go-language-support.md`. |
| 2026-05-26 | Mission execution | All 24 tasks done (env / core seam / GoParser / CLI / packaging / tests / E2E / packaging smoke / docs / regression sweep). Pre-Promise Audit 5/5. 3 distilled lessons archived. |
| 2026-05-26 | Code review (5 angles × 8 candidates) | 15 top + 4 cosmetic findings. |
| 2026-05-26 | Fixes (Groups A–E + cosmetic) | All 15 top findings + 4/5 cosmetics addressed. Commits: `fix(renderer)`, `fix(go) × 3`, `fix(core)`, `fix(misc)`. |
| 2026-05-27 | Real-world validation on `git.tap4fun.com/x15/server` | First run: 32.5 min, 97849 symbols, L1 286 / 1000 tokens. |
| 2026-05-27 | Codegen-skip + budget-filling | After: 10.4 min, 20539 symbols (−79%), L1 997 / 1000 (99.7%). |
| 2026-05-27 | README + changelog + progress | This file. |

## Layered design

```
Source roots ─► Parser plugin (csharp / lua / go)
                            │
                            ▼
                    Symbols + References
                            │
                            ▼
                   Cross-language resolver
                            │
                            ▼
                    PageRank ranker (id-keyed)
                            │
                            ▼
              L1 / L2 / L3 markdown + meta JSON
```

Adding Go required: one new subpackage (`coderepomap/go/`), five id helpers
in `core/identity.py`, four core-seam changes (opt-in `graph_node_kinds` ABC
attr, generator union collection, `build_module_stats` widening, renderer
wording switch). Everything downstream of the parser layer stayed
language-agnostic.

## GoParser feature surface

| Construct | Symbol kind | Reference kinds |
|---|---|---|
| `package foo` | `package` (one per dir) | — |
| `type T struct{…}` / aliases | `class` | embedded type → `inherits` (same-pkg and via import alias) |
| `type T interface{…}` | `interface` | embedded interface → `inherits` |
| Struct field | `field` | — |
| `func F(…)` top-level | `function` | bare-name calls → optimistic same-pkg id |
| `func (r T) M(…)` / `func (r *T) M(…)` | `method` (`lang_meta.receiver_kind`) | bodies walked for `call` / `uses` refs |
| `func (r *Service[T]) Run()` (generic) | `method` (recv_type extracted from `pointer_type(generic_type)`) | same |
| `type Foo[T any] struct{…}` | `class` (`lang_meta.generic_params=["T"]`) | — |
| `import alias "pkg/path"` | — | `import` reference; file-local alias table for downstream call/use resolution; `/vN` semantic-version suffix heuristic for default selectors |
| `// Code generated … DO NOT EDIT.` | `package` only (`lang_meta.generated=True`) | skipped |

## Test coverage

| Surface | Count | File |
|---|---|---|
| Go parser unit tests | 41 | `tests/test_go_parser.py` |
| Go end-to-end generator | 2 | `tests/test_go_generate.py` |
| Source discovery (Go single + multi-lang) | 2 | `tests/test_source_discovery_schema.py` |
| CLI init + packaging smoke | 3 | `tests/test_cli.py` |
| Core seam (graph_node_kinds, build_graph union) | 4 | `tests/test_core_parser_base.py`, `tests/test_core_ranker.py` |
| Renderer (build_module_stats, render_l1/l2 widened) | 9 | `tests/test_review_regressions_phase2.py` |
| Code-review regression cases | 18 | spread across the above |
| C# baseline byte-equivalence | 11 | `tests/test_csharp_parser_baseline.py`, `tests/test_generator_baseline.py` |

**Total: 206 tests pass in ~1.5 s.**

## Lessons archived

`~/.claude/mission-archive/csharp-repomap/lessons/`:

- `2026-05-26-opt-in-widening-attr-for-baseline-safety.md` — gate widening
  on an opt-in `LanguageParser.graph_node_kinds` class attr so byte-
  equivalence is structurally protected.
- `2026-05-26-symbol-signature-must-be-filled.md` — `Symbol.signature` is
  renderer-facing; unit tests miss it. End-to-end generator tests
  mandatory per language.
- `2026-05-26-rare-sentinel-beats-arithmetic-mode-flag.md` — for global
  render-mode switches, trigger on a kind only the new format emits
  (`any(s.kind == "package")`), not an arithmetic comparison that can fire
  on legacy edge cases.

## Out of scope (Phase 1 deliberately)

- Cross-language analysis (Go ↔ C# / Lua / cgo).
- Go-specific presets beyond `generic` (no `gin` / `go-kit` preset).
- Build-tag-aware parsing (`//go:build` filtering).
- Method-set computation for `var _ I = (*T)(nil)` interface satisfaction.
- Reflection / runtime registration tracking.
- Multi-process / parallel parsing (10-min run on 7000 files is acceptable;
  further perf work is a separate effort).

## Known cosmetic invariants preserved

- L1's `_module_from_file` returns the filename for root-level files,
  rendering them as `main.go/` in module overview. This is a v0.1.0
  behavior the C# baseline goldens depend on; not changing it without
  also regenerating goldens.

## Open follow-ups (not blocking ship)

- Lua parser still has the same `Symbol.signature=""` gap that Go's E2E
  test caught — there's no Lua end-to-end test today, so the bug is
  latent. Worth a small follow-up to add a Lua E2E test and populate
  signatures symmetrically.
- `subprocess.run(..., text=True)` in `generator.py:_git` decodes via
  locale codec; non-ASCII branch names on cp936 Windows hosts silently
  yield empty `git_branch`. Pre-existing in C# / Lua code; not introduced
  by the Go PR, but worth a `encoding="utf-8", errors="replace"` patch.
- `coderepomap` is still pinned at version `0.2.0` in `pyproject.toml`.
  The Go addition is feature-level; consider bumping to `0.3.0` before
  the next pypi release.
