# coderepomap

[![PyPI](https://img.shields.io/pypi/v/coderepomap.svg)](https://pypi.org/project/coderepomap/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> 为 AI 编码 Agent 设计的分层多语言代码地图。支持 C# + Lua, 含 U3D 跨语言引用解析, 为 Claude / Cursor / Copilot 优化。

**[English](README.md)** | **简体中文** | **[日本語](README.ja.md)**

---

`coderepomap` 扫描代码库, 生成三层 Markdown — 模块骨架、类签名、引用关系图 — 每层约 1k / 2k / 3k token. AI Agent 只读所需的那一层, 不再翻阅每个文件; token 花在重要代码上, 跨文件引用不再遗漏。

语言支持基于插件: C# 和 Lua 开箱即用。在 Unity + xLua / sLua / ToLua 项目中, Lua 端对 C# 的调用 (`CS.UnityEngine.GameObject`) 会被解析为同一 L3 图中真实的 C# 符号引用。

> [!NOTE]
> v0.2.0 从 `csharp-repomap` 改名而来, **不向后兼容**. v0.1.0 用户: `pip uninstall csharp-repomap && pip install coderepomap[csharp]`.

## 特性

- **三层一预算**. L1 骨架 / L2 签名 / L3 关系图, 各自由可配置 token 预算限制 (装 `tiktoken` 精确计数, 否则按 4 字符/token 估算).
- **多语言插件系统**. C# (`tree-sitter-c-sharp`) 和 Lua (`tree-sitter-lua`) 共享同一 `LanguageParser` 契约; 新增语言只需添加子包.
- **跨语言图**. Lua → C# 引用通过项目级符号索引解析, 多个候选时明示而非静默合并.
- **PageRank 排名**. 重要类浮到每层顶端; 排名可通过 prefix/suffix boost 模式定制.
- **稳定 Symbol ID**. 重载感知 (`csharp:Ns.Type.Method(int,string)`), 区分实例与静态方法 (`lua:mod.T#method` vs `lua:mod.T.f`).
- **Git 钩子**. 一键安装 `post-checkout` / `post-merge` 自动重生成; Windows 桌面通知可选.

## 安装

根据项目类型选 extras:

```bash
pip install coderepomap[csharp]              # C# / Unity
pip install coderepomap[lua]                 # 纯 Lua
pip install coderepomap[csharp,lua,tiktoken] # U3D 混合 + 精确 token
```

> [!TIP]
> `tiktoken` 可选。未装时使用 4 字符/token 估算 — 预算控制够用, 但边界不精确。生产环境推荐安装。

## 快速开始

```bash
cd your-project
repomap init --lang csharp --preset unity   # 或 --lang lua / --preset generic
repomap generate
```

输出位于 `.repomap/output/`:

| 文件 | 说明 |
|---|---|
| `repomap-L1-skeleton.md` | 模块级概览 (约 1k token) |
| `repomap-L2-signatures.md` | 类 / 函数签名 (约 2k token) |
| `repomap-L3-relations.md` | 引用关系图 + 外部引用 (约 3k token) |
| `repomap-meta.json` | 统计、git 提交、ranker 信息 |

把对应层交给 AI Agent 即可。

## 配置

单语言 (`.repomap/config.yaml`):

```yaml
project_name: My Game
lang: csharp                            # 或: lua
source:
  root_path: Assets/Scripts
  exclude_patterns: ["**/Editor/**", "**/Tests/**"]
```

多语言 (Unity + Lua):

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

`repomap init` 写入模板, 根据需要编辑后跑 `repomap generate`.

> [!IMPORTANT]
> 使用 `langs:` 时, loader 自动丢弃默认的 `lang:` — 两者同时出现会报清晰错误. 不含这两个字段的配置回退到 C# 单语言模式 (v0.1.0 兼容).

## 跨语言引用 (Lua → C#)

Lua 解析器对 `CS.X.Y.Z` 链与别名 (`local GO = CS.X.Y; GO.Find(...)`) 生成 `csharp_call` 引用. 解析器:

1. 去除 Lua 侧配置的前缀 (`CS.` / `UnityEngine.` ...).
2. 与项目级 C# 类型索引做精确 FQN 匹配.
3. 链尾为方法名时降级到所属类型.
4. 项目级 short name 查找; 唯一命中即 resolved; 多候选保留 `lang_meta.candidates` 供 L3 审查.

resolved 边进 PageRank 图; unresolved 在 L3 **External References** 章节. 算法细节见 [docs/crosslang.md](docs/crosslang.md).

## 语言支持

| 语言 | 解析器 | 标识符方案 | 特点 |
|---|---|---|---|
| C# | `tree-sitter-c-sharp` + 正则 fallback | `csharp:Ns.Type.Method(paramtypes)` | 命名空间 (含 file-scoped), 嵌套类, 重载感知 ID, Unity 预设 |
| Lua | `tree-sitter-lua` + 正则 fallback | `lua:mod.T#method` (实例), `lua:mod.T.f` (静态) | xLua / sLua / ToLua, 文件级 alias 表, `setmetatable` 继承 |

详见 [docs/lang-csharp.md](docs/lang-csharp.md) 和 [docs/lang-lua.md](docs/lang-lua.md).

## CLI

| 命令 | 参数 | 说明 |
|---|---|---|
| `repomap init` | `--lang csharp\|lua`, `--preset unity\|generic`, `--force` | 写入 `.repomap/config.yaml` |
| `repomap generate` | `--verbose`, `--notify` | 解析源码, 写出 L1/L2/L3/meta |
| `repomap status` | — | 显示上次运行的统计与已注册解析器 |
| `repomap hooks` | `--install` (默认), `--uninstall`, `--with-notify` | 管理 git `post-checkout` / `post-merge` 钩子 |

`python -m coderepomap` 与 `repomap` 等价.

## 工作原理

```
源码根目录 ─► 语言解析器插件 ─► Symbols + References
                                       │
                                       ▼
                              跨语言解析器
                                       │
                                       ▼
                       PageRank ranker (按 Symbol.id 索引)
                                       │
                                       ▼
                       L1 / L2 / L3 markdown + meta JSON
```

分层使每个环节可替换 — 新增语言只需写一个 `LanguageParser` 子类; 更换排名策略不影响解析器.

## 开发

```bash
git clone https://github.com/sputnicyoji/csharp_Repomap
cd csharp_Repomap
python -m venv .venv && .venv/Scripts/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[csharp,lua,tiktoken,dev]
pytest tests/
```

测试 fixture 位于 `tests/fixtures/`. C# 解析器基线 (snapshot + golden markdown) 固定在 `tests/baseline/` — 如需有意重生成参见 `tests/generate_baseline.py`. 124 个测试覆盖解析器插件、ranker、跨语言解析器、CLI 和端到端 generator.

> [!WARNING]
> GitHub 仓库出于历史原因仍叫 `csharp_Repomap`. 包名、CLI、模块名都已是 `coderepomap` (v0.2.0+).
