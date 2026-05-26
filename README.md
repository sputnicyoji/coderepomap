# coderepomap

[![PyPI](https://img.shields.io/pypi/v/coderepomap.svg)](https://pypi.org/project/coderepomap/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> Layered, multi-language code maps for AI coding agents. C# + Lua, U3D cross-language references, tuned for Claude / Cursor / Copilot.

**English** | **[简体中文](README.zh-CN.md)** | **[日本語](README.ja.md)**

---

`coderepomap` scans a codebase and emits three Markdown layers — module skeleton, class signatures, and reference graph — sized to roughly 1k / 2k / 3k tokens. AI agents read the layer they need instead of paging through every file, so they spend tokens on the code that matters and catch cross-file references they would otherwise miss.

Language support is plugin-based: C# and Lua ship in the box. In Unity + xLua / sLua / ToLua projects, Lua-side calls into C# (`CS.UnityEngine.GameObject`) resolve to real C# symbols in the same L3 graph.

> [!NOTE]
> v0.2.0 was renamed from `csharp-repomap` and is **not** backward compatible. v0.1.0 users: `pip uninstall csharp-repomap && pip install coderepomap[csharp]`.

## Features

- **Three layers, one budget.** L1 skeleton / L2 signatures / L3 relations, each capped by a configurable token budget (`tiktoken` precise, 4-chars-per-token fallback).
- **Multi-language plugin system.** C# (`tree-sitter-c-sharp`) and Lua (`tree-sitter-lua`) share one `LanguageParser` contract; add more by dropping in a subpackage.
- **Cross-language graph.** Lua → C# references resolved through a project-wide symbol index, with ambiguous candidates surfaced rather than silently merged.
- **PageRank-ranked output.** Important classes float to the top of every layer; ranking is plugin-tunable via prefix/suffix boost patterns.
- **Stable Symbol IDs.** Overload-aware (`csharp:Ns.Type.Method(int,string)`), instance-vs-static distinct (`lua:mod.T#method` vs `lua:mod.T.f`).
- **Git hooks.** Drop-in `post-checkout` / `post-merge` regeneration; opt-in Windows toast notifier.

## Install

Pick the extras that match your project:

```bash
pip install coderepomap[csharp]              # C# / Unity
pip install coderepomap[lua]                 # pure Lua
pip install coderepomap[csharp,lua,tiktoken] # U3D mixed + precise tokens
```

> [!TIP]
> `tiktoken` is optional. Without it, layers use a 4-chars-per-token fallback — fine for budgeting, less precise at the edge. Install for production accuracy.

## Quick start

```bash
cd your-project
repomap init --lang csharp --preset unity   # or --lang lua / --preset generic
repomap generate
```

Outputs land in `.repomap/output/`:

| File | Description |
|---|---|
| `repomap-L1-skeleton.md` | Module-level overview (~1k tokens) |
| `repomap-L2-signatures.md` | Class / function signatures (~2k tokens) |
| `repomap-L3-relations.md` | Reference graph + external refs (~3k tokens) |
| `repomap-meta.json` | Stats, git commit, ranker info |

Point your AI agent at whichever layer matches the question.

## Configuration

Single-language (`.repomap/config.yaml`):

```yaml
project_name: My Game
lang: csharp                            # or: lua
source:
  root_path: Assets/Scripts
  exclude_patterns: ["**/Editor/**", "**/Tests/**"]
```

Multi-language (Unity + Lua):

```yaml
project_name: Unity + xLua
langs: [csharp, lua]
sources:
  csharp:
    root_path: Assets/Scripts
    exclude_patterns: ["**/Editor/**"]
  lua:
    root_path: Assets/LuaScripts
crosslang:
  enabled: true
  lua_csharp_call_patterns:
    - prefix: "CS."                     # xLua
    # - prefix: "UnityEngine."          # sLua / ToLua
```

`repomap init` writes a starter file; edit before running `repomap generate`.

> [!IMPORTANT]
> When using `langs:` the loader drops the default `lang:` automatically — both keys at once raises a clear error. Configs without either field fall back to single-lang C# (v0.1.0 compatibility).

## Cross-language references (Lua → C#)

The Lua parser emits a `csharp_call` reference for `CS.X.Y.Z` chains and for aliases (`local GO = CS.X.Y; GO.Find(...)`). The resolver:

1. Strips the configured Lua-side prefix (`CS.`, `UnityEngine.`, …).
2. Tries an exact C# FQN match against the project-wide type index.
3. Falls back to the enclosing type when the chain ends in a method name.
4. Falls back to a project-wide short-name lookup; unique → resolved; multiple → unresolved with `lang_meta.candidates` for L3 review.

Resolved edges enter the PageRank graph; unresolved ones surface under **External References** in L3. Algorithm details: [docs/crosslang.md](docs/crosslang.md).

## Language support

| Language | Parser | Identifier scheme | Highlights |
|---|---|---|---|
| C# | `tree-sitter-c-sharp` + regex fallback | `csharp:Ns.Type.Method(paramtypes)` | Namespaces (incl. file-scoped), nested types, overload-aware ids, Unity preset |
| Lua | `tree-sitter-lua` + regex fallback | `lua:mod.T#method` (instance), `lua:mod.T.f` (static) | xLua / sLua / ToLua, file-scope alias table, `setmetatable` inheritance |

See [docs/lang-csharp.md](docs/lang-csharp.md) and [docs/lang-lua.md](docs/lang-lua.md) for what each parser captures and the known limitations carried over from v0.1.0.

## CLI

| Command | Flags | Description |
|---|---|---|
| `repomap init` | `--lang csharp\|lua`, `--preset unity\|generic`, `--force` | Write `.repomap/config.yaml` |
| `repomap generate` | `--verbose`, `--notify`, `--config <path>` | Parse sources, write L1/L2/L3/meta |
| `repomap status` | — | Show last-run stats + registered language parsers |
| `repomap hooks` | `--install` (default), `--uninstall`, `--with-notify` | Manage git `post-checkout` / `post-merge` regeneration |

## Claude Code skill

`skills/repomap/` ships a [Claude Code](https://claude.com/claude-code) skill that drives this CLI from natural-language prompts ("generate code map", "扫一下代码结构 / 生成 repomap"). Install per [skills/README.md](skills/README.md) and the skill auto-detects C# / Lua / mixed projects, runs `python -m coderepomap generate`, and summarizes the result.

`python -m coderepomap` and `repomap` are interchangeable.

## How it works

```
Source roots ─► Parser plugin ─► Symbols + References
                                      │
                                      ▼
                              Cross-language resolver
                                      │
                                      ▼
                         PageRank ranker (Symbol.id keyed)
                                      │
                                      ▼
                          L1 / L2 / L3 markdown + meta JSON
```

The layering keeps each step replaceable — adding a language requires only a new `LanguageParser` subclass; changing the ranking strategy doesn't touch the parsers.

## Development

```bash
git clone https://github.com/sputnicyoji/coderepomap
cd coderepomap
python -m venv .venv && .venv/Scripts/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[csharp,lua,tiktoken,dev]
pytest tests/
```

Test fixtures live in `tests/fixtures/`. The C# parser baseline (snapshot + golden markdown) is pinned in `tests/baseline/` — see `tests/generate_baseline.py` if you intentionally need to regenerate. 124 tests cover the parser plugins, ranker, cross-language resolver, CLI, and end-to-end generator runs.
