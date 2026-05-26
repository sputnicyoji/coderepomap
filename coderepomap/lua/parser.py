#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lua parser plug-in for coderepomap.

Targets U3D-embedded Lua business code (xLua / sLua / ToLua) with a focus on
- module structure (one file = one module path)
- `T.f()` (static-like) vs `T:m()` (instance method) distinction
- `setmetatable(T, {__index = Base})` class inheritance
- `local Foo = require "foo.bar"` alias resolution
- xLua `CS.UnityEngine.Foo` and sLua/ToLua bare-namespace references via
  configurable `lua_csharp_call_patterns`

What we DO emit:
- Symbol(kind='module') one per file
- Symbol(kind='function') for `function name()` (top-level), `local function`,
  and `function T.f()` (no self)
- Symbol(kind='method') for `function T:m()` (implicit self, id uses `#`)
- Symbol(kind='class') for `local Class = setmetatable(...)` patterns and
  `local Class = {}` candidates referenced by `function Class:..()`
- Symbol(kind='field') for top-level `Class.field = value`
- Reference(kind='require') for `require "x"` / `require("x")`
- Reference(kind='inherits') for setmetatable(__index = Base) at module scope
- Reference(kind='call') for resolved Lua function calls (via alias)
- Reference(kind='csharp_call') for `CS.X.Y` or alias chains expanding into one

What we DON'T emit (Phase 7 out-of-scope, per refactor plan):
- Dynamic `loadstring` / `dofile` / `load`
- Multi-level metatable inheritance chains (only one level)
- C extension modules (e.g. `require "cjson"` — emitted as unresolved Lua call)
- Ambiguous CS chain candidates (resolved best-effort, unresolved kept verbatim)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core import identity as ident
from ..core.parser_base import LanguageParser, Reference, Symbol


# --- alias-table model ---------------------------------------------------------

@dataclass
class Alias:
    """File-local binding from a Lua identifier to a resolvable target."""
    kind: str         # 'lua_require' | 'csharp' | 'local_table'
    value: str        # module_id / dotted CS chain / table name


# --- Parser --------------------------------------------------------------------

class LuaParser(LanguageParser):
    lang = "lua"
    file_extensions = [".lua"]
    default_excludes = [
        "**/test/**",
        "**/Test/**",
        "**/tests/**",
        "**/.luarocks/**",
        "**/.git/**",
        "**/node_modules/**",
    ]
    default_boost_patterns: List[dict] = []
    default_categories = {"Other": {"patterns": []}}

    def __init__(self, csharp_call_prefixes: Optional[List[str]] = None):
        """
        Args:
            csharp_call_prefixes: Top-level identifiers that mark a Lua->C#
                call chain. Default `["CS."]` for xLua. Pass `["UnityEngine."]`
                (or both) for sLua / ToLua. Also set per-project via
                `crosslang.lua_csharp_call_patterns` in config (consumed by
                `configure()`).
        """
        self._parser = None
        self._language = None
        self._initialized = False
        # Strip trailing `.` so we can compare bare top-level identifiers
        self._csharp_call_prefixes = [
            p.rstrip(".") for p in (csharp_call_prefixes or ["CS"])
        ]

    def configure(self, config: dict) -> None:
        """Pull `crosslang.lua_csharp_call_patterns` from config if present.

        Pattern entries shape: `[{prefix: "CS."}, {prefix: "UnityEngine."}]`.
        Empty list / missing -> keep ctor default (`["CS"]`, xLua-flavored).
        """
        cl = (config or {}).get("crosslang", {}) or {}
        patterns = cl.get("lua_csharp_call_patterns", []) or []
        prefixes = [p.get("prefix", "").rstrip(".") for p in patterns if p.get("prefix")]
        if prefixes:
            self._csharp_call_prefixes = prefixes

    # --- TS init ---------------------------------------------------------------

    def _init_parser(self) -> bool:
        if self._initialized:
            return self._parser is not None
        try:
            import tree_sitter_lua as ts_lua
            from tree_sitter import Language, Parser

            self._language = Language(ts_lua.language())
            self._parser = Parser(self._language)
            self._initialized = True
            return True
        except ImportError as e:
            print(f"Warning: tree-sitter-lua not available: {e}", file=sys.stderr)
            print("Falling back to regex-based Lua parsing", file=sys.stderr)
            self._initialized = True
            return False
        except Exception as e:
            print(f"Warning: tree-sitter-lua init failed: {e}", file=sys.stderr)
            self._initialized = True
            return False

    # --- public entry ----------------------------------------------------------

    def parse_file(self, file_path: Path, base_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                content = file_path.read_bytes().decode("utf-8", errors="ignore")

        try:
            rel = str(file_path.relative_to(base_path)).replace("\\", "/")
        except ValueError:
            # Path outside base (Windows junction / symlink etc) — fall back
            # to the absolute path so we still emit symbols, just with a
            # non-module-shaped id derived from the filename.
            rel = str(file_path).replace("\\", "/")
        module_id = self._module_id_from_rel(rel)

        if self._init_parser():
            return self._parse_with_ts(content, rel, module_id)
        return self._parse_with_regex(content, rel, module_id)

    # --- module id helpers -----------------------------------------------------

    @staticmethod
    def _module_id_from_rel(rel: str) -> str:
        """`foo/bar.lua` -> `foo.bar`."""
        if rel.endswith(".lua"):
            rel = rel[: -len(".lua")]
        return rel.replace("/", ".")

    @staticmethod
    def _normalize_require_path(s: str) -> str:
        """`foo.bar` / `foo/bar` / `foo\\bar` -> `foo.bar`."""
        return s.replace("\\", "/").replace("/", ".")

    # --- TS walk ---------------------------------------------------------------

    def _parse_with_ts(self, content: str, rel: str, module_id: str) -> Tuple[List[Symbol], List[Reference]]:
        symbols: List[Symbol] = []
        references: List[Reference] = []

        content_bytes = content.encode("utf-8")
        try:
            tree = self._parser.parse(content_bytes)
        except (RuntimeError, MemoryError, OSError, Exception) as e:
            # tree-sitter C extension can fail on huge / corrupt files. Fall
            # back to the regex path rather than aborting the whole run.
            print(
                f"Warning: tree-sitter-lua failed on {rel}: {type(e).__name__}: {e}. "
                f"Falling back to regex.",
                file=sys.stderr,
            )
            return self._parse_with_regex(content, rel, module_id)
        root = tree.root_node

        # Always emit the module symbol
        symbols.append(Symbol(
            id=ident.lua_module_symbol_id(module_id),
            name=module_id.rsplit(".", 1)[-1] if "." in module_id else module_id,
            fqn=module_id,
            kind="module",
            file=rel,
            line=1,
            lang="lua",
        ))

        aliases: Dict[str, Alias] = {}
        # Tables introduced via `local T = {}` (candidate class container)
        local_tables: Dict[str, int] = {}  # name -> line

        # Single AST walk; module scope vs function-body scope tracked by callsite
        self._walk_module(
            root, content_bytes, rel, module_id, aliases, local_tables, symbols, references
        )

        # After walk, promote any local_table that is the receiver of a method
        # definition (T:m) to a class symbol, so it shows up in the graph.
        method_parents = {
            s.parent for s in symbols if s.kind == "method" and s.parent
        }
        function_table_parents = {
            s.parent for s in symbols if s.kind == "function" and s.parent
        }
        for name, line in local_tables.items():
            if name in method_parents or name in function_table_parents:
                # Skip if we already added a class symbol via setmetatable
                if any(s.kind == "class" and s.name == name for s in symbols):
                    continue
                symbols.append(Symbol(
                    id=ident.csharp_member_id("", "", "")  # placeholder, replaced below
                    if False else f"lua:{module_id}.{name}",
                    name=name,
                    fqn=f"{module_id}.{name}",
                    kind="class",
                    file=rel,
                    line=line,
                    container=module_id,
                    parent="",
                    lang="lua",
                ))

        return symbols, references

    # --- AST helpers ----------------------------------------------------------

    @staticmethod
    def _text(node, src_bytes: bytes) -> str:
        try:
            return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _line(node) -> int:
        return node.start_point[0] + 1

    def _walk_module(
        self,
        node,
        src_bytes: bytes,
        rel: str,
        module_id: str,
        aliases: Dict[str, Alias],
        local_tables: Dict[str, int],
        symbols: List[Symbol],
        references: List[Reference],
    ) -> None:
        """Top-level (chunk) walker. Recurses into nested blocks via _walk_block."""
        for child in node.children:
            self._visit_top(child, src_bytes, rel, module_id, aliases, local_tables, symbols, references)

    def _visit_top(
        self,
        node,
        src_bytes: bytes,
        rel: str,
        module_id: str,
        aliases: Dict[str, Alias],
        local_tables: Dict[str, int],
        symbols: List[Symbol],
        references: List[Reference],
    ) -> None:
        t = node.type
        if t == "variable_declaration":
            self._handle_variable_declaration(
                node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
                is_local=True,
            )
        elif t == "assignment_statement":
            # Module-scope `T.field = value` etc.
            self._handle_assignment(
                node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
            )
        elif t == "function_declaration":
            self._handle_function_declaration(
                node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
            )
        elif t == "function_call":
            self._handle_call(node, src_bytes, rel, module_id, aliases, symbols, references)
        elif t in ("block", "chunk", "return_statement", "expression_list"):
            for c in node.children:
                self._visit_top(c, src_bytes, rel, module_id, aliases, local_tables, symbols, references)

    def _handle_variable_declaration(
        self, node, src_bytes, rel, module_id, aliases, local_tables, symbols, references, is_local,
    ):
        assign = next((c for c in node.children if c.type == "assignment_statement"), None)
        if assign is None:
            return
        self._handle_assignment(
            assign, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
        )

    def _handle_assignment(
        self, node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
    ):
        var_list = next((c for c in node.children if c.type == "variable_list"), None)
        expr_list = next((c for c in node.children if c.type == "expression_list"), None)
        if var_list is None or expr_list is None:
            return

        var_nodes = [c for c in var_list.children if c.type != "," and c.type != "variable_list"]
        expr_nodes = [c for c in expr_list.children if c.type != ","]

        # Handle Lua multi-return: `local A, B = factory()` has 2 vars + 1 expr.
        # zip silently drops B; we want B to still be recognized as a local
        # table candidate (no alias info because we can't see the return).
        for i, var_node in enumerate(var_nodes):
            expr_node = expr_nodes[i] if i < len(expr_nodes) else None
            self._handle_one_binding(
                var_node, expr_node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
            )

    def _handle_one_binding(
        self, var_node, expr_node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
    ):
        var_text = self._text(var_node, src_bytes).strip()
        # Single identifier (most common): `local X = ...` or `X = ...`
        if var_node.type == "identifier":
            name = var_text
            line = self._line(var_node)

            # Multi-return Lua case: `local A, B = factory()` — B has no
            # corresponding expr_node. Register B as a local_table candidate
            # so subsequent `B.method()` references can resolve module-locally.
            if expr_node is None:
                local_tables[name] = line
                aliases[name] = Alias("local_table", name)
                return

            # 1. require?
            req_path = self._extract_require(expr_node, src_bytes)
            if req_path is not None:
                aliases[name] = Alias("lua_require", req_path)
                references.append(Reference(
                    from_id=ident.lua_module_symbol_id(module_id),
                    to_id=f"lua:{req_path}",  # may be unresolved at parse time; crosslang/post-resolve fixes
                    file=rel,
                    line=line,
                    kind="require",
                    lang="lua",
                    resolved=False,  # post-resolve in generator can flip to True if module symbol exists
                    to_external=req_path,
                ))
                return

            # 2. CS chain?
            cs_chain = self._extract_csharp_chain(expr_node, src_bytes)
            if cs_chain is not None:
                aliases[name] = Alias("csharp", cs_chain)
                return

            # 3. setmetatable({}, {__index = Base}) — class with inheritance
            mt = self._extract_setmetatable_inherit(expr_node, src_bytes)
            if mt is not None:
                base = mt
                symbols.append(Symbol(
                    id=f"lua:{module_id}.{name}",
                    name=name,
                    fqn=f"{module_id}.{name}",
                    kind="class",
                    file=rel,
                    line=line,
                    container=module_id,
                    parent=base,
                    lang="lua",
                    lang_meta={"base": base},
                ))
                # inherit edge — resolved lazily via aliases (Base might be a require'd module)
                base_alias = aliases.get(base)
                if base_alias and base_alias.kind == "lua_require":
                    references.append(Reference(
                        from_id=f"lua:{module_id}.{name}",
                        to_id=f"lua:{base_alias.value}",
                        file=rel, line=line, kind="inherits", lang="lua",
                        resolved=False,  # post-resolve in generator
                        to_external=base_alias.value,
                    ))
                else:
                    references.append(Reference(
                        from_id=f"lua:{module_id}.{name}",
                        to_id="",
                        file=rel, line=line, kind="inherits", lang="lua",
                        resolved=False,
                        to_external=base,
                    ))
                aliases[name] = Alias("local_table", name)
                return

            # 4. table_constructor `{}` — candidate class
            # 5. function_call (factory pattern: `local T = SomeFactory()`)
            #    — we don't know the return shape, but recording as local_table
            #    lets subsequent `T.method()` calls resolve module-locally
            #    rather than fall through to unresolved global. This mirrors
            #    v0.1.0 legacy behavior where bare names always made it into
            #    the graph as nodes.
            if expr_node.type in ("table_constructor", "function_call"):
                local_tables[name] = line
                aliases[name] = Alias("local_table", name)
                return

            # 6. Otherwise (string / number / binary expr): no symbol emitted

    def _extract_require(self, expr_node, src_bytes: bytes) -> Optional[str]:
        """If expr is `require "x"` / `require('x')`, return normalized path."""
        if expr_node.type != "function_call":
            return None
        callee = expr_node.children[0] if expr_node.children else None
        if callee is None or callee.type != "identifier":
            return None
        if self._text(callee, src_bytes) != "require":
            return None
        # Find argument string
        args = next((c for c in expr_node.children if c.type == "arguments"), None)
        if args is None:
            # could be `require "x"` without parens — child of function_call is string directly
            for c in expr_node.children[1:]:
                if c.type == "string":
                    return self._string_content(c, src_bytes)
            return None
        for c in args.children:
            if c.type == "string":
                return self._string_content(c, src_bytes)
        return None

    def _string_content(self, str_node, src_bytes: bytes) -> str:
        """Extract `string_content` child; fall back to stripping quotes."""
        for c in str_node.children:
            if c.type == "string_content":
                return self._normalize_require_path(self._text(c, src_bytes))
        raw = self._text(str_node, src_bytes).strip()
        if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
            return self._normalize_require_path(raw[1:-1])
        return self._normalize_require_path(raw)

    def _extract_csharp_chain(self, expr_node, src_bytes: bytes) -> Optional[str]:
        """Return dotted chain like 'CS.UnityEngine.GameObject' if expr is one."""
        chain = self._collect_dot_chain(expr_node, src_bytes)
        if not chain:
            return None
        # First segment must match a configured csharp prefix root
        root = chain.split(".", 1)[0]
        if root not in self._csharp_call_prefixes:
            return None
        return chain

    def _collect_dot_chain(self, expr_node, src_bytes: bytes) -> Optional[str]:
        """Recursively flatten `a.b.c` (dot_index_expression chain) into 'a.b.c'.

        Returns None if expression isn't a pure dot chain ending in identifiers.
        """
        if expr_node.type == "identifier":
            return self._text(expr_node, src_bytes)
        if expr_node.type == "dot_index_expression":
            parts = []
            cur = expr_node
            while cur is not None and cur.type == "dot_index_expression":
                # children: lhs, '.', rhs(identifier)
                non_dot = [c for c in cur.children if c.type != "."]
                if len(non_dot) != 2:
                    return None
                lhs, rhs = non_dot
                if rhs.type != "identifier":
                    return None
                parts.append(self._text(rhs, src_bytes))
                cur = lhs
            if cur is None or cur.type != "identifier":
                return None
            parts.append(self._text(cur, src_bytes))
            return ".".join(reversed(parts))
        return None

    def _extract_setmetatable_inherit(self, expr_node, src_bytes: bytes) -> Optional[str]:
        """Detect `setmetatable({...} or T, {__index = Base})`, return Base name."""
        if expr_node.type != "function_call":
            return None
        callee = expr_node.children[0] if expr_node.children else None
        if callee is None or callee.type != "identifier":
            return None
        if self._text(callee, src_bytes) != "setmetatable":
            return None
        args = next((c for c in expr_node.children if c.type == "arguments"), None)
        if args is None:
            return None
        # arguments: ( arg1 , arg2 )
        meaningful = [c for c in args.children if c.type not in ("(", ")", ",")]
        if len(meaningful) < 2:
            return None
        mt_arg = meaningful[1]
        # mt_arg should be a table_constructor with field `__index = Base`
        if mt_arg.type != "table_constructor":
            return None
        for c in mt_arg.children:
            if c.type == "field":
                # field children: [identifier, '=', value] or [name=, value]
                non_eq = [x for x in c.children if x.type != "="]
                if len(non_eq) == 2:
                    key_node, val_node = non_eq
                    if key_node.type == "identifier" and self._text(key_node, src_bytes) == "__index":
                        if val_node.type == "identifier":
                            return self._text(val_node, src_bytes)
                        chain = self._collect_dot_chain(val_node, src_bytes)
                        if chain:
                            return chain
        return None

    def _handle_function_declaration(
        self, node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
    ):
        # children: function, name_expr, parameters, block, end
        name_expr = None
        block_node = None
        for c in node.children:
            if c.type in ("identifier", "dot_index_expression", "method_index_expression"):
                if name_expr is None:
                    name_expr = c
            elif c.type == "block":
                block_node = c

        if name_expr is None:
            return

        line = self._line(node)
        if name_expr.type == "identifier":
            fname = self._text(name_expr, src_bytes)
            symbols.append(Symbol(
                id=ident.lua_function_id(module_id, fname),
                name=fname,
                fqn=f"{module_id}.{fname}",
                kind="function",
                file=rel,
                line=line,
                container=module_id,
                parent="",
                lang="lua",
            ))
        elif name_expr.type == "dot_index_expression":
            # function T.f() — no self
            non_dot = [c for c in name_expr.children if c.type != "."]
            if len(non_dot) == 2 and all(x.type == "identifier" for x in non_dot):
                tbl, fname = (self._text(x, src_bytes) for x in non_dot)
                symbols.append(Symbol(
                    id=ident.lua_function_id(module_id, fname, table=tbl),
                    name=fname,
                    fqn=f"{module_id}.{tbl}.{fname}",
                    kind="function",
                    file=rel,
                    line=line,
                    container=module_id,
                    parent=tbl,
                    lang="lua",
                ))
        elif name_expr.type == "method_index_expression":
            non_colon = [c for c in name_expr.children if c.type != ":"]
            if len(non_colon) == 2 and all(x.type == "identifier" for x in non_colon):
                tbl, fname = (self._text(x, src_bytes) for x in non_colon)
                symbols.append(Symbol(
                    id=ident.lua_method_id(module_id, tbl, fname),
                    name=fname,
                    fqn=f"{module_id}.{tbl}#{fname}",
                    kind="method",
                    file=rel,
                    line=line,
                    container=module_id,
                    parent=tbl,
                    lang="lua",
                ))

        # Walk the body for any calls / nested decls
        if block_node is not None:
            self._walk_block(
                block_node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
            )

    def _walk_block(
        self, block_node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
    ):
        for c in block_node.children:
            self._visit_block(
                c, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
            )

    def _visit_block(
        self, node, src_bytes, rel, module_id, aliases, local_tables, symbols, references,
    ):
        t = node.type
        if t == "function_call":
            self._handle_call(node, src_bytes, rel, module_id, aliases, symbols, references)
        elif t in ("if_statement", "for_statement", "while_statement", "repeat_statement",
                   "block", "do_statement", "expression_list", "return_statement"):
            for c in node.children:
                self._visit_block(c, src_bytes, rel, module_id, aliases, local_tables, symbols, references)
        elif t == "variable_declaration":
            # Inner local: no module-level alias, but recurse for sub-calls
            self._handle_variable_declaration_inside_body(
                node, src_bytes, rel, module_id, aliases, symbols, references,
            )

    def _handle_variable_declaration_inside_body(
        self, node, src_bytes, rel, module_id, aliases, symbols, references,
    ):
        # Walk to find calls (for require / setmetatable / function_call); we
        # don't extend aliases at function-body scope to avoid leaking back to
        # module scope. Only chase expressions for references.
        assign = next((c for c in node.children if c.type == "assignment_statement"), None)
        if assign is None:
            return
        expr_list = next((c for c in assign.children if c.type == "expression_list"), None)
        if expr_list is None:
            return
        for c in expr_list.children:
            self._visit_block(c, src_bytes, rel, module_id, aliases, {}, symbols, references)

    def _handle_call(
        self, call_node, src_bytes, rel, module_id, aliases, symbols, references,
    ):
        if not call_node.children:
            return
        callee = call_node.children[0]
        line = self._line(call_node)

        # `require "x"` at body scope still emits a require reference
        if callee.type == "identifier" and self._text(callee, src_bytes) == "require":
            req = self._extract_require(call_node, src_bytes)
            if req is not None:
                references.append(Reference(
                    from_id=ident.lua_module_symbol_id(module_id),
                    to_id=f"lua:{req}",
                    file=rel, line=line, kind="require", lang="lua",
                    resolved=False,
                    to_external=req,
                ))
            return

        # dot_index_expression: e.g. `Foo.Init(...)`
        chain = self._collect_dot_chain(callee, src_bytes) if callee.type == "dot_index_expression" else None
        # method_index_expression: e.g. `GO:Find(...)`
        is_method_call = False
        method_target = None
        method_receiver = None
        if callee.type == "method_index_expression":
            non_colon = [c for c in callee.children if c.type != ":"]
            if len(non_colon) == 2 and all(x.type == "identifier" for x in non_colon):
                recv, meth = (self._text(x, src_bytes) for x in non_colon)
                method_receiver = recv
                method_target = meth
                is_method_call = True

        from_id = ident.lua_module_symbol_id(module_id)

        if chain is not None:
            root = chain.split(".", 1)[0]
            tail = chain.split(".", 1)[1] if "." in chain else ""

            # CS prefix (literal `CS.X.Y` form)
            if root in self._csharp_call_prefixes:
                references.append(Reference(
                    from_id=from_id, to_id="", file=rel, line=line,
                    kind="csharp_call", lang="lua", resolved=False,
                    to_external=chain,
                ))
                return

            # alias resolution
            alias = aliases.get(root)
            if alias is None:
                # global / unknown root — treat as unresolved Lua call
                references.append(Reference(
                    from_id=from_id, to_id="", file=rel, line=line,
                    kind="call", lang="lua", resolved=False,
                    to_external=chain,
                ))
                return

            if alias.kind == "csharp":
                full_chain = f"{alias.value}.{tail}" if tail else alias.value
                references.append(Reference(
                    from_id=from_id, to_id="", file=rel, line=line,
                    kind="csharp_call", lang="lua", resolved=False,
                    to_external=full_chain,
                ))
                return

            if alias.kind == "lua_require":
                target_module = alias.value
                # tail can be empty (direct module call) or path within module
                target_id = f"lua:{target_module}" + (f".{tail}" if tail else "")
                references.append(Reference(
                    from_id=from_id, to_id=target_id, file=rel, line=line,
                    kind="call", lang="lua", resolved=False,  # post-resolve
                    to_external=f"{target_module}{('.' + tail) if tail else ''}",
                ))
                return

            if alias.kind == "local_table":
                target_id = f"lua:{module_id}.{root}" + (f".{tail}" if tail else "")
                references.append(Reference(
                    from_id=from_id, to_id=target_id, file=rel, line=line,
                    kind="call", lang="lua", resolved=False,
                    to_external=f"{root}{('.' + tail) if tail else ''}",
                ))
                return

        if is_method_call and method_receiver and method_target:
            alias = aliases.get(method_receiver)
            if alias and alias.kind == "csharp":
                full_chain = f"{alias.value}.{method_target}"
                references.append(Reference(
                    from_id=from_id, to_id="", file=rel, line=line,
                    kind="csharp_call", lang="lua", resolved=False,
                    to_external=full_chain,
                ))
                return
            if alias and alias.kind == "lua_require":
                target_id = f"lua:{alias.value}#{method_target}"
                references.append(Reference(
                    from_id=from_id, to_id=target_id, file=rel, line=line,
                    kind="call", lang="lua", resolved=False,
                    to_external=f"{alias.value}#{method_target}",
                ))
                return
            if alias and alias.kind == "local_table":
                target_id = f"lua:{module_id}.{method_receiver}#{method_target}"
                references.append(Reference(
                    from_id=from_id, to_id=target_id, file=rel, line=line,
                    kind="call", lang="lua", resolved=False,
                    to_external=f"{method_receiver}#{method_target}",
                ))
                return

    # --- Regex fallback --------------------------------------------------------

    def _parse_with_regex(self, content: str, rel: str, module_id: str) -> Tuple[List[Symbol], List[Reference]]:
        """Conservative regex fallback when tree-sitter-lua is unavailable.

        Captures only the bare minimum needed for L1/L2 visibility:
        - module symbol
        - top-level function/method declarations
        - require references
        """
        symbols: List[Symbol] = []
        references: List[Reference] = []

        symbols.append(Symbol(
            id=ident.lua_module_symbol_id(module_id),
            name=module_id.rsplit(".", 1)[-1] if "." in module_id else module_id,
            fqn=module_id,
            kind="module",
            file=rel, line=1, lang="lua",
        ))

        # function T:m / T.f / name
        for m in re.finditer(r"^\s*function\s+([A-Za-z_]\w*)\s*\(", content, flags=re.MULTILINE):
            name = m.group(1)
            line = content[: m.start()].count("\n") + 1
            symbols.append(Symbol(
                id=ident.lua_function_id(module_id, name),
                name=name, fqn=f"{module_id}.{name}", kind="function",
                file=rel, line=line, container=module_id, lang="lua",
            ))
        for m in re.finditer(r"^\s*function\s+([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(", content, flags=re.MULTILINE):
            tbl, name = m.group(1), m.group(2)
            line = content[: m.start()].count("\n") + 1
            symbols.append(Symbol(
                id=ident.lua_function_id(module_id, name, table=tbl),
                name=name, fqn=f"{module_id}.{tbl}.{name}", kind="function",
                file=rel, line=line, container=module_id, parent=tbl, lang="lua",
            ))
        for m in re.finditer(r"^\s*function\s+([A-Za-z_]\w*):([A-Za-z_]\w*)\s*\(", content, flags=re.MULTILINE):
            tbl, name = m.group(1), m.group(2)
            line = content[: m.start()].count("\n") + 1
            symbols.append(Symbol(
                id=ident.lua_method_id(module_id, tbl, name),
                name=name, fqn=f"{module_id}.{tbl}#{name}", kind="method",
                file=rel, line=line, container=module_id, parent=tbl, lang="lua",
            ))

        from_id = ident.lua_module_symbol_id(module_id)
        for m in re.finditer(r"require\s*\(?\s*['\"]([^'\"]+)['\"]\s*\)?", content):
            path = self._normalize_require_path(m.group(1))
            line = content[: m.start()].count("\n") + 1
            references.append(Reference(
                from_id=from_id, to_id=f"lua:{path}", file=rel, line=line,
                kind="require", lang="lua", resolved=False, to_external=path,
            ))

        return symbols, references


__all__ = ["LuaParser", "Alias"]
