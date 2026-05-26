---
name: repomap
description: Generate a three-layered code map (L1 skeleton / L2 signatures / L3 reference graph) of a target codebase, tuned for AI coding agents. Use when the user asks to scan / index / summarize / map a code repository, build a project overview for Claude / Cursor / Copilot, refresh repomap, generate code map, surface cross-file references, dependency overview, or PageRank class importance. Chinese triggers: 生成 repomap / 更新代码地图 / 扫一下代码结构 / 建代码地图 / 给 AI 看 / 代码骨架 / 重要的类是哪几个 / 跨文件引用 / U3D 跨语言. Supports C# (Unity / U3D) and Lua (xLua / sLua / ToLua) and resolves Lua → C# cross-language calls.
---

# repomap

Drive the `coderepomap` CLI (PyPI: `coderepomap`) to produce three layered Markdown outputs (~1k / 2k / 3k tokens) plus a `meta.json`, sized for AI agent context windows. Works for any codebase, any size.

## When to use

The user wants to:

- generate / refresh / update the repo map for the current project
- give an AI agent a layered overview of a codebase
- scan a Unity / C# / Lua project for class / method / reference structure
- get a cross-language graph for U3D mixed C# + Lua (xLua / sLua / ToLua)
- audit which classes / functions PageRank surfaces as central

Triggers like "update repomap", "generate code map", "scan repo for Claude", "build project skeleton", "index codebase", `/repomap` all map here.

## When NOT to use

- Reading a single file or symbol — use Read / Grep / LSP directly.
- Pure documentation generation (use `create-readme` / `codewiki-extract`).
- Semantic search / RAG queries — repomap is structural, not embeddings.

## Outputs (always produced)

| File | Purpose | Default budget |
|---|---|---|
| `repomap-L1-skeleton.md` | Module-level overview | ~1k tokens |
| `repomap-L2-signatures.md` | Class / function signatures | ~2k tokens |
| `repomap-L3-relations.md` | Reference graph + external refs | ~3k tokens |
| `repomap-meta.json` | Stats + git commit / branch + ranker stats | — |

Default output directory: `.repomap/output/`. Per-config override possible.

## Prerequisites

1. **Python 3.8+** and `pip` available.
2. **coderepomap** package installed with appropriate extras:

   ```bash
   pip install --user coderepomap[csharp]                  # C# only
   pip install --user coderepomap[lua]                     # Lua only
   pip install --user coderepomap[csharp,lua,tiktoken]     # U3D mixed + precise tokens
   ```

   Pre-PyPI install from a local wheel (DEV ONLY — replace with the actual
   wheel location on the current machine; the path below is the author's
   working tree and will not exist on other machines):
   ```bash
   pip install --user "<path/to/dist/coderepomap-0.2.0-py3-none-any.whl>[csharp,lua,tiktoken]"
   # Example (author's tree): G:/Github/csharp_Repomap/dist/coderepomap-0.2.0-py3-none-any.whl
   ```

3. **Invocation form**: always use `python -m coderepomap ...` (not `repomap` directly), because user-scripts dir is often not on PATH on Windows.

## Steps

### Step 0 — Lock the target + confirm before any writes

Before scanning anything, establish two facts:

1. **Target directory**. Default = current cwd. If the user said
   `/repomap <path>` or "scan G:/foo" or "扫一下 X1", parse out the path
   and use that. Otherwise confirm with the user: "I'll scan `<cwd>` —
   correct?" before proceeding.

2. **Write permissions you'll need**. Announce up front what will be
   written, and pause for confirmation before any of these:
   - **Writing `.repomap/config.yaml`** (Step 2a / 2b) — overwrites only
     if `--force`, but creates a new directory; mention it.
   - **Installing git hooks** (`hooks --install`) — modifies
     `.git/hooks/post-checkout` and `.git/hooks/post-merge`; this affects
     EVERY collaborator's local repo after they pull the hook files.
     ALWAYS confirm with the user explicitly. Show them what the hooks
     will do.
   - Re-running `generate` against an EXISTING custom config: safe (only
     writes to `output.directory` configured), but if that path is shared
     with other tooling (e.g. X1-style `.claude/context/`), say so.

Read-only probes in Step 1 don't need confirmation.

### Step 1 — Detect project shape

Probe the target directory (default: `cwd`, override below) with TOOLS THAT
ACTUALLY RECURSE — `ls *.lua` / `ls **/*.lua` are unreliable in git-bash
where `globstar` defaults off and `*` doesn't descend.

Recommended probes:

- **Glob tool** (preferred when running inside Claude Code):
  - `Glob "**/*.cs"` — limit head to first ~5 results
  - `Glob "**/*.lua"` — first ~5
  - `Glob "**/*.csproj"` and `Glob "**/*.sln"`
  - `Read` `ProjectSettings/ProjectVersion.txt` if it exists (Unity sentinel)
  - `Read` `.repomap/config.yaml` and any project-specific config you spot
    (e.g. `.claude/context/*/config.yaml`, `repomap/config.yaml`) — DON'T
    overwrite a custom config; use `--config <path>` instead (Step 2c).

- **Bash fallback** (when Glob unavailable):
  ```bash
  find . -maxdepth 6 -name "*.csproj" -o -name "*.sln" 2>/dev/null | head -5
  find . -maxdepth 6 -name "*.cs"  2>/dev/null | head -5
  find . -maxdepth 6 -name "*.lua" 2>/dev/null | head -5
  test -f ProjectSettings/ProjectVersion.txt && echo "unity"
  find . -maxdepth 4 -name "config.yaml" -path "*repomap*" 2>/dev/null
  ```

Classify based on what files actually exist anywhere under the target root:

| Signal (anywhere in tree) | Pick |
|---|---|
| `.cs` present + Unity sentinel (`ProjectSettings/ProjectVersion.txt`) + NO `.lua` | `--lang csharp --preset unity` |
| `.cs` present, no Unity sentinel, no `.lua` | `--lang csharp` (generic preset) |
| `.lua` present, no `.cs` | `--lang lua` |
| **Both `.cs` AND `.lua` present** (Unity or not — server + scripts, embedded Lua, anything) | multi-lang config (write manually, see Step 2b) |
| Neither | bail with `"No C#/Lua sources detected at <target>. Specify --lang or initialize manually."` |

### Step 2 — Initialize config (only if `.repomap/config.yaml` doesn't already exist)

#### 2a — Single language (init via CLI)

```bash
python -m coderepomap init --lang csharp --preset unity        # or other combos
```

This writes `.repomap/config.yaml` from the bundled template.

#### 2b — Multi-language (write config directly)

`repomap init` is single-lang. For C# + Lua mixed, write `.repomap/config.yaml` manually:

```yaml
project_name: "<repo name>"
langs: [csharp, lua]
sources:
  csharp:
    root_path: "Assets/Scripts"     # adapt to project
    exclude_patterns: ["**/Editor/**", "**/Tests/**", "**/bin/**", "**/obj/**"]
  lua:
    root_path: "Assets/LuaScripts"  # adapt to project
    exclude_patterns: ["**/test/**"]
tokens:
  l1_skeleton: 1000
  l2_signatures: 2000
  l3_relations: 3000
  encoding: cl100k_base
pagerank:
  alpha: 0.85
  max_iter: 100
output:
  directory: ".repomap/output"
  files:
    skeleton: repomap-L1-skeleton.md
    signatures: repomap-L2-signatures.md
    relations: repomap-L3-relations.md
    meta: repomap-meta.json
importance_boost:
  patterns: []
  priority_modules: []
categories:
  Other: {patterns: []}
crosslang:
  enabled: true
  lua_csharp_call_patterns:
    - prefix: "CS."           # xLua default
    # - prefix: "UnityEngine."  # uncomment for sLua / ToLua
```

#### 2c — Custom config path (existing projects with their own layout)

If the project already has a config at a non-default location (e.g. `.claude/context/repomap-generator/config.yaml`), use `--config <path>` and DO NOT call `init`. The custom config's own `output.directory` is honored.

### Step 3 — Generate

Default config:
```bash
python -m coderepomap generate --verbose
```

Custom config path:
```bash
python -m coderepomap generate --config <path-to-yaml> --verbose
```

Verify exit code = 0 and the 4 output files exist.

### Step 4 — Report

Summarize back to the user:

- File counts (from meta.json `stats.file_count`)
- Symbol / reference counts (`stats.class_count`, `stats.method_count`, `stats.reference_count`)
- Token sizes of each layer (printed by `--verbose`)
- Top 3-5 modules by PageRank rank (from meta.json `top_modules`)
- For multi-lang runs: count of resolved Lua → C# `csharp_call` edges (look in L3 markdown)

Sample summary format (REPLACE every `<N>` / `<module>` placeholder with the
actual values from the just-completed run — do NOT carry numbers from any
unrelated project):

```
Repomap generated for <project_name>:
- <N> source files / <N> symbols / <N> references / <N> modules
- L1: <N> tokens | L2: <N> tokens | L3: <N> tokens
- Top modules: <module1> (<N> classes), <module2> (<N>), <module3> (<N>)
- Output: <output.directory from config>
- Duration: <s>s
```

## Optional follow-ups

- `python -m coderepomap status` — show last run's stats + registered parsers.
- `python -m coderepomap hooks --install` — install `post-checkout` / `post-merge` git hooks for auto-refresh on branch switches and pulls. Confirm with the user before running — hooks affect collaborator workflow.

## Important constraints

> [!IMPORTANT]
> - The CLI command is `python -m coderepomap`. Do NOT call `repomap.exe` directly — it's installed under the user-scripts directory which is usually not on PATH on Windows.
> - When using `langs:` in config, the loader auto-drops the default `lang:`. Don't manually set both.
> - The default `_get_default_config` ships empty `importance_boost.patterns`; the generator falls back to each parser plug-in's `default_boost_patterns` (e.g. C# gets Manager/Service/Controller suffix boost). User configs can override.
> - For sLua / ToLua (bare-namespace style), set `crosslang.lua_csharp_call_patterns` to `[{prefix: "UnityEngine."}]`. Default `["CS."]` is xLua.

## Failure modes & recovery

| Symptom | Cause | Fix |
|---|---|---|
| `No parser plug-in for lang 'csharp'` | extras not installed | `pip install --user coderepomap[csharp]` |
| `config has both lang and langs` | hand-edited config has both keys | Pick one; `langs:` wins if you want multi-lang |
| `No configuration found at ...` | `.repomap/config.yaml` missing | Run Step 2 (`init` or write manually) |
| `Source root for lang=... not found` | `source.root_path` wrong | Edit config; verify with `ls <root>` |
| Multi-lang config raises ValueError via legacy code | running v0.1.0 (`csharp-repomap`) | Uninstall: `pip uninstall csharp-repomap`; install `coderepomap` |
| Lua tree-sitter wheel ABI error | mismatched Python ABI | `pip install --user --upgrade coderepomap[lua]` |
| Token sizes look off / approximate | `tiktoken` not installed; using 4-chars-per-token fallback | `pip install --user coderepomap[tiktoken]` for precise counts |
| `generate` very slow / appears hung on huge repo (50k+ files) | tree-sitter parsing serially; this IS the throughput limit | Wait — typical large Unity project (~5k .cs files) takes ~30s. For 50k+ tighten `source.exclude_patterns` to skip generated / vendored code |
| Output files exist but L1/L2/L3 are nearly empty | `source.root_path` points at a dir with no matching files OR every file got excluded | Re-check root_path; remove overly broad `exclude_patterns` like `**/*` |

## Reference

- Package: [`coderepomap`](https://github.com/sputnicyoji/coderepomap) (PyPI when published)
- Cross-language algorithm: `docs/crosslang.md` in the repo
- C# / Lua parser specifics: `docs/lang-csharp.md`, `docs/lang-lua.md`
