# coderepomap

[![PyPI](https://img.shields.io/pypi/v/coderepomap.svg)](https://pypi.org/project/coderepomap/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> AI コーディング Agent のためのレイヤード多言語コードマップ。C# + Lua 対応、U3D クロスランゲージ参照解析つき、Claude / Cursor / Copilot 向け最適化。

**[English](README.md)** | **[简体中文](README.zh-CN.md)** | **日本語**

---

`coderepomap` はコードベースをスキャンし、モジュールスケルトン・クラスシグネチャ・参照グラフという 3 層の Markdown を出力します。各層は約 1k / 2k / 3k トークンに収まるよう調整されています。AI Agent は必要なレイヤだけを読めばよく、全ファイルをめくる必要がありません。重要なコードにトークンを集中でき、ファイル間の参照を見落とすこともなくなります。

言語サポートはプラグイン式です。C# と Lua は同梱されています。Unity + xLua / sLua / ToLua プロジェクトでは、Lua 側から C# 型への呼び出し (`CS.UnityEngine.GameObject`) が同じ L3 グラフ内で実際の C# シンボル参照として解決されます。

> [!NOTE]
> v0.2.0 で `csharp-repomap` から改名されました。**後方互換性はありません**。v0.1.0 ユーザー: `pip uninstall csharp-repomap && pip install coderepomap[csharp]`。

## 特徴

- **3 層 1 予算**。L1 スケルトン / L2 シグネチャ / L3 参照グラフ。各層は設定可能なトークン予算で制限されます (`tiktoken` で正確、未インストール時は 4 文字/トークンのフォールバック)。
- **多言語プラグイン**。C# (`tree-sitter-c-sharp`) と Lua (`tree-sitter-lua`) は同じ `LanguageParser` 契約を共有。サブパッケージを追加するだけで新しい言語を組み込めます。
- **クロスランゲージグラフ**。Lua → C# 参照はプロジェクトワイドのシンボルインデックスで解決。曖昧な候補はサイレントマージせず明示します。
- **PageRank ベースのランキング**。重要なクラスが各層の上位に浮かびます。prefix/suffix のブーストパターンで調整可能。
- **安定した Symbol ID**。オーバーロード対応 (`csharp:Ns.Type.Method(int,string)`)、インスタンス/スタティックの区別 (`lua:mod.T#method` vs `lua:mod.T.f`)。
- **Git フック**。`post-checkout` / `post-merge` で自動再生成。Windows トースト通知もオプションで利用可。

## インストール

プロジェクトに合った extras を選択:

```bash
pip install coderepomap[csharp]              # C# / Unity
pip install coderepomap[lua]                 # 純粋な Lua
pip install coderepomap[csharp,lua,tiktoken] # U3D 混合 + 正確なトークン計数
```

> [!TIP]
> `tiktoken` はオプションです。未インストール時は 4 文字/トークンのフォールバックを使用 — 予算管理には十分ですが、境界付近では精度が落ちます。本番環境ではインストール推奨。

## クイックスタート

```bash
cd your-project
repomap init --lang csharp --preset unity   # または --lang lua / --preset generic
repomap generate
```

出力は `.repomap/output/` に生成されます:

| ファイル | 説明 |
|---|---|
| `repomap-L1-skeleton.md` | モジュールレベル概要 (約 1k トークン) |
| `repomap-L2-signatures.md` | クラス / 関数シグネチャ (約 2k トークン) |
| `repomap-L3-relations.md` | 参照グラフ + 外部参照 (約 3k トークン) |
| `repomap-meta.json` | 統計、git コミット、ranker 情報 |

質問に応じたレイヤを AI Agent に渡してください。

## 設定

単一言語 (`.repomap/config.yaml`):

```yaml
project_name: My Game
lang: csharp                            # または: lua
source:
  root_path: Assets/Scripts
  exclude_patterns: ["**/Editor/**", "**/Tests/**"]
```

多言語 (Unity + Lua):

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

`repomap init` がテンプレートを書き出します。必要に応じて編集してから `repomap generate` を実行してください。

> [!IMPORTANT]
> `langs:` を使用すると、ローダーがデフォルトの `lang:` を自動的に削除します — 両方同時に存在すると明確なエラーが出ます。どちらもない設定は単一言語 C# モードにフォールバックします (v0.1.0 互換)。

## クロスランゲージ参照 (Lua → C#)

Lua パーサーは `CS.X.Y.Z` チェーンとエイリアス (`local GO = CS.X.Y; GO.Find(...)`) に対して `csharp_call` 参照を生成します。リゾルバは:

1. 設定された Lua 側プレフィックス (`CS.` / `UnityEngine.` …) を剥がす。
2. プロジェクト全体の C# 型インデックスに対して厳密 FQN マッチを試みる。
3. チェーンが末尾にメソッド名を持つ場合、外側の型にフォールバック。
4. プロジェクト全体の short-name 検索 — 唯一一致なら resolved、複数候補は `lang_meta.candidates` を残し L3 でレビュー可能に。

resolved エッジは PageRank グラフに入ります。unresolved は L3 の **External References** セクションに出ます。アルゴリズム詳細は [docs/crosslang.md](docs/crosslang.md)。

## 言語サポート

| 言語 | パーサー | 識別子スキーマ | 特徴 |
|---|---|---|---|
| C# | `tree-sitter-c-sharp` + 正規表現フォールバック | `csharp:Ns.Type.Method(paramtypes)` | 名前空間 (file-scoped 含む)、ネスト型、オーバーロード対応 ID、Unity プリセット |
| Lua | `tree-sitter-lua` + 正規表現フォールバック | `lua:mod.T#method` (インスタンス)、`lua:mod.T.f` (スタティック) | xLua / sLua / ToLua、ファイルスコープエイリアステーブル、`setmetatable` 継承 |

詳細は [docs/lang-csharp.md](docs/lang-csharp.md) と [docs/lang-lua.md](docs/lang-lua.md) を参照。

## CLI

| コマンド | フラグ | 説明 |
|---|---|---|
| `repomap init` | `--lang csharp\|lua`, `--preset unity\|generic`, `--force` | `.repomap/config.yaml` を生成 |
| `repomap generate` | `--verbose`, `--notify` | ソースを解析し L1/L2/L3/meta を出力 |
| `repomap status` | — | 最終実行の統計と登録済みパーサーを表示 |
| `repomap hooks` | `--install` (デフォルト), `--uninstall`, `--with-notify` | git `post-checkout` / `post-merge` フックの管理 |

`python -m coderepomap` と `repomap` は等価です。

## 仕組み

```
ソースルート ─► 言語パーサープラグイン ─► Symbols + References
                                              │
                                              ▼
                                クロスランゲージリゾルバ
                                              │
                                              ▼
                            PageRank ranker (Symbol.id キー)
                                              │
                                              ▼
                            L1 / L2 / L3 markdown + meta JSON
```

レイヤリングにより各ステップが置き換え可能 — 新言語の追加は `LanguageParser` サブクラスの追加だけで済み、ランキング戦略の変更はパーサーに影響しません。

## 開発

```bash
git clone https://github.com/sputnicyoji/csharp_Repomap
cd csharp_Repomap
python -m venv .venv && .venv/Scripts/activate    # PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[csharp,lua,tiktoken,dev]
pytest tests/
```

テスト fixture は `tests/fixtures/` にあります。C# パーサーのベースライン (snapshot + golden markdown) は `tests/baseline/` に固定されています — 意図的に再生成する場合は `tests/generate_baseline.py` を参照。124 のテストがパーサープラグイン、ranker、クロスランゲージリゾルバ、CLI、エンドツーエンドの generator 実行をカバーします。

> [!WARNING]
> GitHub リポジトリは歴史的な理由で依然として `csharp_Repomap` という名前です。パッケージ、CLI、モジュール名はすべて `coderepomap` (v0.2.0+) です。
