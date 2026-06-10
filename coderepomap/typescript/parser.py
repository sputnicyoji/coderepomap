#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TypeScript parser plug-in for coderepomap.

Targets generic TypeScript / TSX sources: one module symbol per file, one
package symbol per directory (the renderer's widened-mode sentinel, mirroring
Go), class / interface / function / method analysis via tree-sitter-typescript,
a file-local import binding table, and optimistic cross-file id prediction for
crosslang post-resolve.

Phase 1 scope:
- Symbol: package / module / class (incl. enums) / interface (incl. type
  aliases) / function (incl. top-level arrow consts) / method
- Reference: import (per named binding) / call / uses (new-expressions) /
  inherits / implements
- Tree-sitter primary path (separate TS / TSX dialects), regex fallback
- ESM-style relative specifiers: `./x.js` -> `x.ts`, directory imports
  resolve through `index.ts`; bare specifiers (npm / node builtins) stay
  unresolved and render under L3 External References
- No cross-language analysis in Phase 1
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..core import identity as ident
from ..core.parser_base import LanguageParser, Reference, Symbol


@dataclass
class _ImportBinding:
    """One file-local import binding: local name -> predicted target id."""
    local: str       # how the binding is referenced in code
    target_id: str   # predicted `typescript:` id; "" when the source is external
    path: str        # original import specifier text
    is_namespace: bool = False
    type_only: bool = False


# Node types that introduce a top-level declaration we emit symbols for.
_DECL_TYPES = frozenset({
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "function_declaration",
    "generator_function_declaration",
    "lexical_declaration",
    "variable_declaration",
    "type_alias_declaration",
    "enum_declaration",
})

# Relative-import extensions stripped before file-system resolution. ESM
# TypeScript imports name the COMPILED file (`./search.js`) while the source
# on disk is `./search.ts` — the specifier extension is routing noise.
_STRIP_IMPORT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")


class TypeScriptParser(LanguageParser):
    lang = "typescript"
    file_extensions = [".ts", ".tsx"]
    graph_node_kinds = ["package", "module", "class", "interface", "function"]
    default_excludes = [
        "**/node_modules/**",
        "**/dist/**",
        "**/build/**",
        "**/out/**",
        "**/coverage/**",
        "**/.git/**",
        # Test conventions — Vitest / Jest style co-located tests and
        # snapshot/mock directories.
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
        "**/__tests__/**",
        "**/__mocks__/**",
        "**/__snapshots__/**",
        # Ambient declaration files describe shapes, not behavior; their
        # dotted stems (`foo.d`) would also collide with crosslang's
        # trailing-segment trim.
        "**/*.d.ts",
    ]
    default_boost_patterns = [
        {"suffix": "Service",    "boost": 1.8, "description": "Service layer"},
        {"suffix": "Controller", "boost": 1.7, "description": "Controllers"},
        {"suffix": "Manager",    "boost": 1.5, "description": "Manager"},
        {"suffix": "Provider",   "boost": 1.5, "description": "Provider pattern"},
        {"suffix": "Repository", "boost": 1.5, "description": "Data access"},
        {"suffix": "Store",      "boost": 1.4, "description": "State management"},
        {"suffix": "Handler",    "boost": 1.4, "description": "Event handlers"},
        {"suffix": "Adapter",    "boost": 1.4, "description": "Adapter pattern"},
        {"suffix": "Factory",    "boost": 1.3, "description": "Factory pattern"},
    ]
    default_categories = {
        "Core":    {"patterns": ["core", "app", "main", "index"]},
        "API":     {"patterns": ["api", "routes", "controller", "handler", "server"]},
        "Service": {"patterns": ["service", "services"]},
        "Domain":  {"patterns": ["model", "entity", "domain", "types", "schema"]},
        "Storage": {"patterns": ["store", "repository", "storage", "db"]},
        "UI":      {"patterns": ["component", "page", "view", "ui"]},
        "Utils":   {"patterns": ["util", "helper", "lib", "shared"]},
        "Config":  {"patterns": ["config", "settings"]},
        "Other":   {"patterns": []},
    }

    def __init__(self):
        self._ts_parser = None
        self._tsx_parser = None
        self._initialized = False
        self._parsed_packages: set = set()

    # --- tree-sitter init ----------------------------------------------------

    def _init_parser(self) -> bool:
        if self._initialized:
            return self._ts_parser is not None
        try:
            import tree_sitter_typescript as ts_ts
            from tree_sitter import Language, Parser

            # tree-sitter-typescript ships two grammars; TSX is a superset
            # syntax that changes how `<` parses, so each dialect needs its
            # own parser instance.
            self._ts_parser = Parser(Language(ts_ts.language_typescript()))
            self._tsx_parser = Parser(Language(ts_ts.language_tsx()))
            self._initialized = True
            return True
        except ImportError as e:
            # Do NOT latch _initialized on failure: a transient ImportError
            # shouldn't permanently downgrade the whole run to regex mode.
            # Matches GoParser's policy.
            print(f"Warning: tree-sitter-typescript not available: {e}", file=sys.stderr)
            print("Falling back to regex-based TypeScript parsing", file=sys.stderr)
            self._ts_parser = None
            self._tsx_parser = None
            return False
        except Exception as e:
            print(f"Warning: tree-sitter-typescript init failed: {e}", file=sys.stderr)
            self._ts_parser = None
            self._tsx_parser = None
            return False

    # --- entry ----------------------------------------------------------------

    def parse_file(self, file_path: Path, base_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                content = file_path.read_bytes().decode("utf-8", errors="ignore")

        try:
            rel = file_path.resolve().relative_to(base_path.resolve()).as_posix()
        except ValueError:
            rel = file_path.as_posix()

        module_id = ident.ts_module_id_from_path(file_path, base_path)
        rel_dir = module_id.rsplit("/", 1)[0] if "/" in module_id else ""

        symbols: List[Symbol] = []
        self._emit_structural_symbols(symbols, rel, module_id, rel_dir)

        if self._init_parser():
            return self._parse_with_ts(
                content, rel, module_id, file_path, base_path, symbols,
            )
        return self._parse_with_regex(content, rel, module_id, symbols)

    def _emit_structural_symbols(
        self, symbols: List[Symbol], rel: str, module_id: str, rel_dir: str,
    ) -> None:
        """Emit the per-directory package symbol (deduped) and the per-file
        module symbol. Root-level files get no package symbol — there is no
        meaningful directory to represent."""
        if rel_dir:
            package_id = ident.ts_package_id(rel_dir)
            if package_id not in self._parsed_packages:
                self._parsed_packages.add(package_id)
                display = rel_dir.rsplit("/", 1)[-1]
                symbols.append(Symbol(
                    id=package_id,
                    name=display,
                    fqn=rel_dir,
                    kind="package",
                    file=rel,
                    line=1,
                    signature=f"directory {rel_dir}/",
                    lang="typescript",
                    lang_meta={"rel_dir": rel_dir},
                ))
        stem = module_id.rsplit("/", 1)[-1]
        symbols.append(Symbol(
            id=ident.ts_module_symbol_id(module_id),
            name=stem,
            fqn=module_id,
            kind="module",
            file=rel,
            line=1,
            signature=f"module {module_id}",
            container=ident.ts_package_id(rel_dir) if rel_dir else "",
            lang="typescript",
        ))

    # --- relative-import resolution --------------------------------------------

    @staticmethod
    def _resolve_relative_module(
        spec: str, file_path: Path, base_path: Path,
    ) -> Optional[str]:
        """Resolve a relative import specifier to a module_id, or None when the
        target is outside the source root.

        Checks the file system so `./search.js` finds `search.ts` and
        `./gutenberg` finds `gutenberg/index.ts`. A specifier whose file
        doesn't exist still returns the normalized guess — crosslang simply
        won't find a match and the ref stays unresolved (honest external).
        """
        body = spec
        for ext in _STRIP_IMPORT_EXTS:
            if body.endswith(ext):
                body = body[: -len(ext)]
                break
        target = (file_path.parent / body).resolve()
        try:
            rel = target.relative_to(base_path.resolve())
        except ValueError:
            return None
        rel_posix = rel.as_posix()
        if rel_posix == ".":
            rel_posix = "index"
        for candidate in (f"{rel_posix}.ts", f"{rel_posix}.tsx"):
            if (base_path / candidate).is_file():
                return rel_posix
        for candidate in (f"{rel_posix}/index.ts", f"{rel_posix}/index.tsx"):
            if (base_path / candidate).is_file():
                return f"{rel_posix}/index"
        return rel_posix

    # --- tree-sitter walker -----------------------------------------------------

    def _parse_with_ts(
        self, content: str, rel: str, module_id: str,
        file_path: Path, base_path: Path, symbols: List[Symbol],
    ) -> Tuple[List[Symbol], List[Reference]]:
        references: List[Reference] = []
        content_bytes = content.encode("utf-8")
        parser = self._tsx_parser if rel.endswith(".tsx") else self._ts_parser
        try:
            tree = parser.parse(content_bytes)
        except Exception as e:
            print(
                f"Warning: tree-sitter-typescript failed on {rel}: "
                f"{type(e).__name__}: {e}. Falling back to regex.",
                file=sys.stderr,
            )
            return self._parse_with_regex(content, rel, module_id, symbols)

        root = tree.root_node
        from_id = ident.ts_module_symbol_id(module_id)

        # Pass 1: imports + re-exports populate the binding table; top-level
        # declaration names are collected so later bodies can reference
        # earlier-OR-later declarations (function hoisting).
        bindings: Dict[str, _ImportBinding] = {}
        toplevel_names: Set[str] = set()   # names we emit symbols for
        toplevel_locals: Set[str] = set()  # plain consts — suppress, don't link
        decl_nodes: List[Tuple[object, bool]] = []  # (node, exported)
        exported_names: Set[str] = set()   # `export { a, b }` / `export default a`
        walk_only_nodes: List[object] = []

        for child in root.children:
            t = child.type
            if t == "import_statement":
                self._handle_import(
                    child, content_bytes, rel, from_id,
                    file_path, base_path, bindings, references,
                )
            elif t == "export_statement":
                source = next((c for c in child.children if c.type == "string"), None)
                if source is not None:
                    self._handle_reexport(
                        child, source, content_bytes, rel, from_id,
                        file_path, base_path, references,
                    )
                    continue
                inner = next((c for c in child.children if c.type in _DECL_TYPES), None)
                if inner is not None:
                    decl_nodes.append((inner, True))
                    continue
                clause = next((c for c in child.children if c.type == "export_clause"), None)
                if clause is not None:
                    for spec in clause.children:
                        if spec.type != "export_specifier":
                            continue
                        name_node = next(
                            (c for c in spec.children if c.type == "identifier"), None,
                        )
                        if name_node is not None:
                            exported_names.add(self._text(name_node, content_bytes))
                    continue
                # `export default <expression>` — mark identifier defaults,
                # walk the rest for refs.
                default_ident = next(
                    (c for c in child.children if c.type == "identifier"), None,
                )
                if default_ident is not None:
                    exported_names.add(self._text(default_ident, content_bytes))
                else:
                    walk_only_nodes.append(child)
            elif t in _DECL_TYPES:
                decl_nodes.append((child, False))
            elif t == "expression_statement":
                walk_only_nodes.append(child)

        for node, _exported in decl_nodes:
            for name, callable_like in self._decl_names(node, content_bytes):
                if callable_like:
                    toplevel_names.add(name)
                else:
                    toplevel_locals.add(name)

        ctx = _WalkContext(
            src=content_bytes, rel=rel, module_id=module_id, from_id=from_id,
            bindings=bindings, toplevel_names=toplevel_names,
            toplevel_locals=toplevel_locals, references=references,
        )

        # Pass 2: emit declaration symbols, heritage refs, and body refs.
        for node, exported in decl_nodes:
            self._emit_declaration(node, exported, symbols, ctx)
        for node in walk_only_nodes:
            self._walk_body(node, set(), ctx)

        # Pass 3: `export { a, b }` marks already-emitted symbols exported.
        if exported_names:
            for sym in symbols:
                if sym.name in exported_names and sym.lang == "typescript":
                    if sym.kind in ("class", "interface", "function"):
                        sym.lang_meta["exported"] = True

        return symbols, references

    # --- declaration name pre-scan ----------------------------------------------

    def _decl_names(self, node, src_bytes) -> List[Tuple[str, bool]]:
        """Return (name, callable_like) pairs a declaration introduces.

        callable_like names become same-file reference targets; plain consts
        are treated like locals so calls through them don't emit edges.
        """
        t = node.type
        out: List[Tuple[str, bool]] = []
        if t in ("class_declaration", "abstract_class_declaration",
                 "interface_declaration", "type_alias_declaration"):
            n = next((c for c in node.children if c.type == "type_identifier"), None)
            if n is not None:
                out.append((self._text(n, src_bytes), True))
        elif t in ("function_declaration", "generator_function_declaration",
                   "enum_declaration"):
            n = next((c for c in node.children if c.type == "identifier"), None)
            if n is not None:
                out.append((self._text(n, src_bytes), True))
        elif t in ("lexical_declaration", "variable_declaration"):
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                n = next((c for c in decl.children if c.type == "identifier"), None)
                if n is None:
                    continue
                value = decl.children[-1] if decl.children else None
                is_fn = value is not None and value.type in (
                    "arrow_function", "function_expression", "function",
                )
                out.append((self._text(n, src_bytes), is_fn))
        return out

    # --- declaration emission -----------------------------------------------------

    def _emit_declaration(self, node, exported: bool, symbols, ctx) -> None:
        t = node.type
        if t in ("class_declaration", "abstract_class_declaration"):
            self._emit_class(node, exported, symbols, ctx)
        elif t == "interface_declaration":
            self._emit_interface(node, exported, symbols, ctx)
        elif t in ("function_declaration", "generator_function_declaration"):
            self._emit_function(node, exported, symbols, ctx)
        elif t in ("lexical_declaration", "variable_declaration"):
            self._emit_variable_decl(node, exported, symbols, ctx)
        elif t == "type_alias_declaration":
            self._emit_type_alias(node, exported, symbols, ctx)
        elif t == "enum_declaration":
            self._emit_enum(node, exported, symbols, ctx)

    def _emit_class(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src)
        is_abstract = node.type == "abstract_class_declaration"
        type_params = self._type_params(node, src)

        extends_names: List[str] = []
        implements_names: List[str] = []
        heritage = next((c for c in node.children if c.type == "class_heritage"), None)
        if heritage is not None:
            for clause in heritage.children:
                if clause.type == "extends_clause":
                    base = self._heritage_target(clause, src)
                    if base:
                        extends_names.append(base)
                        self._emit_heritage_ref(name, base, "inherits", node, ctx)
                elif clause.type == "implements_clause":
                    for tn in clause.children:
                        iface = self._named_type_text(tn, src)
                        if iface:
                            implements_names.append(iface)
                            self._emit_heritage_ref(name, iface, "implements", node, ctx)

        sig = f"{'abstract ' if is_abstract else ''}class {name}{type_params}"
        if extends_names:
            sig += f" extends {extends_names[0]}"
        if implements_names:
            sig += f" implements {', '.join(implements_names)}"

        symbols.append(Symbol(
            id=ident.ts_decl_id(ctx.module_id, name),
            name=name,
            fqn=f"{ctx.module_id}.{name}",
            kind="class",
            file=ctx.rel,
            line=self._line(node),
            signature=self._truncate(sig),
            container=ctx.from_id,
            lang="typescript",
            lang_meta={"exported": exported, "abstract": is_abstract},
        ))

        body = next((c for c in node.children if c.type == "class_body"), None)
        if body is not None:
            self._emit_class_members(body, name, symbols, ctx)

    def _emit_class_members(self, body, class_name: str, symbols, ctx) -> None:
        src = ctx.src
        for member in body.children:
            if member.type == "method_definition":
                name_node = next(
                    (c for c in member.children
                     if c.type in ("property_identifier", "private_property_identifier")),
                    None,
                )
                if name_node is None:
                    continue
                name = self._text(name_node, src)
                mods = self._member_modifiers(member, src)
                sig = self._callable_signature(name, member, src, prefix=mods)
                symbols.append(Symbol(
                    id=ident.ts_method_id(ctx.module_id, class_name, name),
                    name=name,
                    fqn=f"{ctx.module_id}.{class_name}.{name}",
                    kind="method",
                    file=ctx.rel,
                    line=self._line(member),
                    signature=sig,
                    container=ctx.from_id,
                    parent=class_name,
                    lang="typescript",
                    lang_meta={
                        "static": "static" in mods,
                        "async": "async" in mods,
                        "visibility": (
                            "private" if "private" in mods
                            else "protected" if "protected" in mods
                            else "public"
                        ),
                    },
                ))
                block = next(
                    (c for c in member.children if c.type == "statement_block"), None,
                )
                if block is not None:
                    params = self._collect_param_names(member, src)
                    self._walk_body(block, params, ctx)
            elif member.type == "public_field_definition":
                # Class property arrow methods: `handle = (msg) => {...}`.
                name_node = next(
                    (c for c in member.children if c.type == "property_identifier"),
                    None,
                )
                value = member.children[-1] if member.children else None
                if name_node is None or value is None:
                    continue
                if value.type not in ("arrow_function", "function_expression", "function"):
                    continue
                name = self._text(name_node, src)
                sig = self._callable_signature(name, value, src, prefix="")
                symbols.append(Symbol(
                    id=ident.ts_method_id(ctx.module_id, class_name, name),
                    name=name,
                    fqn=f"{ctx.module_id}.{class_name}.{name}",
                    kind="method",
                    file=ctx.rel,
                    line=self._line(member),
                    signature=sig,
                    container=ctx.from_id,
                    parent=class_name,
                    lang="typescript",
                    lang_meta={"static": False, "async": False, "visibility": "public",
                               "arrow_property": True},
                ))
                params = self._collect_param_names(value, src)
                self._walk_body(value, params, ctx)

    def _emit_interface(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src)
        type_params = self._type_params(node, src)

        extends_clause = next(
            (c for c in node.children if c.type == "extends_type_clause"), None,
        )
        extends_names: List[str] = []
        if extends_clause is not None:
            for tn in extends_clause.children:
                base = self._named_type_text(tn, src)
                if base:
                    extends_names.append(base)
                    self._emit_heritage_ref(name, base, "inherits", node, ctx)

        sig = f"interface {name}{type_params}"
        if extends_names:
            sig += f" extends {', '.join(extends_names)}"

        symbols.append(Symbol(
            id=ident.ts_decl_id(ctx.module_id, name),
            name=name,
            fqn=f"{ctx.module_id}.{name}",
            kind="interface",
            file=ctx.rel,
            line=self._line(node),
            signature=self._truncate(sig),
            container=ctx.from_id,
            lang="typescript",
            lang_meta={"exported": exported},
        ))

        body = next((c for c in node.children if c.type in ("object_type", "interface_body")), None)
        if body is None:
            return
        for member in body.children:
            if member.type != "method_signature":
                continue
            mn = next((c for c in member.children if c.type == "property_identifier"), None)
            if mn is None:
                continue
            mname = self._text(mn, src)
            symbols.append(Symbol(
                id=ident.ts_method_id(ctx.module_id, name, mname),
                name=mname,
                fqn=f"{ctx.module_id}.{name}.{mname}",
                kind="method",
                file=ctx.rel,
                line=self._line(member),
                signature=self._callable_signature(mname, member, src, prefix=""),
                container=ctx.from_id,
                parent=name,
                lang="typescript",
                lang_meta={"interface_member": True},
            ))

    def _emit_function(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src)
        is_async = any(c.type == "async" for c in node.children)
        sig = self._callable_signature(
            name, node, src, prefix=f"{'async ' if is_async else ''}function",
        )
        symbols.append(Symbol(
            id=ident.ts_decl_id(ctx.module_id, name),
            name=name,
            fqn=f"{ctx.module_id}.{name}",
            kind="function",
            file=ctx.rel,
            line=self._line(node),
            signature=sig,
            container=ctx.from_id,
            lang="typescript",
            lang_meta={"exported": exported, "async": is_async},
        ))
        block = next((c for c in node.children if c.type == "statement_block"), None)
        if block is not None:
            params = self._collect_param_names(node, src)
            self._walk_body(block, params, ctx)

    def _emit_variable_decl(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        for decl in node.children:
            if decl.type != "variable_declarator":
                continue
            name_node = next((c for c in decl.children if c.type == "identifier"), None)
            value = decl.children[-1] if decl.children else None
            if value is None:
                continue
            if name_node is not None and value.type in (
                "arrow_function", "function_expression", "function",
            ):
                name = self._text(name_node, src)
                is_async = any(c.type == "async" for c in value.children)
                params = next(
                    (c for c in value.children if c.type == "formal_parameters"), None,
                )
                ptext = self._squash(self._text(params, src)) if params is not None else "(...)"
                ret = next((c for c in value.children if c.type == "type_annotation"), None)
                rtext = self._squash(self._text(ret, src)) if ret is not None else ""
                sig = f"const {name} = {'async ' if is_async else ''}{ptext}{rtext} =>"
                symbols.append(Symbol(
                    id=ident.ts_decl_id(ctx.module_id, name),
                    name=name,
                    fqn=f"{ctx.module_id}.{name}",
                    kind="function",
                    file=ctx.rel,
                    line=self._line(decl),
                    signature=self._truncate(sig),
                    container=ctx.from_id,
                    lang="typescript",
                    lang_meta={"exported": exported, "async": is_async,
                               "arrow_const": True},
                ))
                fn_params = self._collect_param_names(value, src)
                self._walk_body(value, fn_params, ctx)
            else:
                # Plain const — no symbol, but its initializer may call
                # imported factories (`export const tool = createTool(...)`).
                self._walk_body(value, set(), ctx)

    def _emit_type_alias(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        name_node = next((c for c in node.children if c.type == "type_identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src)
        symbols.append(Symbol(
            id=ident.ts_decl_id(ctx.module_id, name),
            name=name,
            fqn=f"{ctx.module_id}.{name}",
            kind="interface",
            file=ctx.rel,
            line=self._line(node),
            signature=f"type {name}{self._type_params(node, src)}",
            container=ctx.from_id,
            lang="typescript",
            lang_meta={"exported": exported, "declaration_form": "type_alias"},
        ))

    def _emit_enum(self, node, exported: bool, symbols, ctx) -> None:
        src = ctx.src
        name_node = next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src)
        symbols.append(Symbol(
            id=ident.ts_decl_id(ctx.module_id, name),
            name=name,
            fqn=f"{ctx.module_id}.{name}",
            kind="class",
            file=ctx.rel,
            line=self._line(node),
            signature=f"enum {name}",
            container=ctx.from_id,
            lang="typescript",
            lang_meta={"exported": exported, "declaration_form": "enum"},
        ))

    # --- imports / re-exports ------------------------------------------------------

    def _handle_import(
        self, node, src_bytes, rel, from_id, file_path, base_path,
        bindings: Dict[str, _ImportBinding], references: List[Reference],
    ) -> None:
        source = next((c for c in node.children if c.type == "string"), None)
        if source is None:
            return
        spec = self._string_text(source, src_bytes)
        type_only = any(c.type == "type" for c in node.children)
        line = self._line(node)

        target_module: Optional[str] = None
        if spec.startswith("."):
            target_module = self._resolve_relative_module(spec, file_path, base_path)

        clause = next((c for c in node.children if c.type == "import_clause"), None)
        emitted_any = False
        if clause is not None:
            for c in clause.children:
                if c.type == "identifier":
                    # default import — the local name says nothing about the
                    # exported symbol; bind at module level.
                    local = self._text(c, src_bytes)
                    target = ident.ts_module_symbol_id(target_module) if target_module else ""
                    bindings[local] = _ImportBinding(
                        local=local, target_id=target, path=spec, type_only=type_only,
                    )
                    if target:
                        references.append(Reference(
                            from_id=from_id, to_id=target, to_external=spec,
                            file=rel, line=line, kind="import", lang="typescript",
                            resolved=False,
                            lang_meta={"type_only": type_only} if type_only else {},
                        ))
                        emitted_any = True
                elif c.type == "namespace_import":
                    ns = next((x for x in c.children if x.type == "identifier"), None)
                    if ns is None:
                        continue
                    local = self._text(ns, src_bytes)
                    target = ident.ts_module_symbol_id(target_module) if target_module else ""
                    bindings[local] = _ImportBinding(
                        local=local, target_id=target, path=spec,
                        is_namespace=True, type_only=type_only,
                    )
                    if target:
                        references.append(Reference(
                            from_id=from_id, to_id=target, to_external=spec,
                            file=rel, line=line, kind="import", lang="typescript",
                            resolved=False,
                            lang_meta={"type_only": type_only} if type_only else {},
                        ))
                        emitted_any = True
                elif c.type == "named_imports":
                    for spec_node in c.children:
                        if spec_node.type != "import_specifier":
                            continue
                        idents = [
                            x for x in spec_node.children if x.type == "identifier"
                        ]
                        if not idents:
                            continue
                        exported_name = self._text(idents[0], src_bytes)
                        local = self._text(idents[-1], src_bytes)
                        spec_type_only = type_only or any(
                            x.type == "type" for x in spec_node.children
                        )
                        if target_module:
                            target = ident.ts_decl_id(target_module, exported_name)
                        else:
                            target = ""
                        bindings[local] = _ImportBinding(
                            local=local, target_id=target, path=spec,
                            type_only=spec_type_only,
                        )
                        if target:
                            references.append(Reference(
                                from_id=from_id, to_id=target, to_external=spec,
                                file=rel, line=self._line(spec_node),
                                kind="import", lang="typescript", resolved=False,
                                lang_meta={"type_only": spec_type_only}
                                if spec_type_only else {},
                            ))
                            emitted_any = True

        if not emitted_any:
            if target_module:
                # side-effect import of an in-tree file
                references.append(Reference(
                    from_id=from_id,
                    to_id=ident.ts_module_symbol_id(target_module),
                    to_external=spec,
                    file=rel, line=line, kind="import", lang="typescript",
                    resolved=False,
                ))
            else:
                # bare specifier (npm package / node builtin) — one external
                # ref per import statement, not per binding, to keep L3 lean.
                references.append(Reference(
                    from_id=from_id, to_id="", to_external=spec,
                    file=rel, line=line, kind="import", lang="typescript",
                    resolved=False,
                    lang_meta={"type_only": type_only} if type_only else {},
                ))

    def _handle_reexport(
        self, node, source, src_bytes, rel, from_id, file_path, base_path,
        references: List[Reference],
    ) -> None:
        spec = self._string_text(source, src_bytes)
        line = self._line(node)
        target_module: Optional[str] = None
        if spec.startswith("."):
            target_module = self._resolve_relative_module(spec, file_path, base_path)
        if not target_module:
            references.append(Reference(
                from_id=from_id, to_id="", to_external=spec,
                file=rel, line=line, kind="import", lang="typescript",
                resolved=False, lang_meta={"reexport": True},
            ))
            return
        clause = next((c for c in node.children if c.type == "export_clause"), None)
        if clause is not None:
            for spec_node in clause.children:
                if spec_node.type != "export_specifier":
                    continue
                idents = [x for x in spec_node.children if x.type == "identifier"]
                if not idents:
                    continue
                exported_name = self._text(idents[0], src_bytes)
                references.append(Reference(
                    from_id=from_id,
                    to_id=ident.ts_decl_id(target_module, exported_name),
                    to_external=spec,
                    file=rel, line=self._line(spec_node),
                    kind="import", lang="typescript", resolved=False,
                    lang_meta={"reexport": True},
                ))
        else:
            # `export * from './x.js'`
            references.append(Reference(
                from_id=from_id,
                to_id=ident.ts_module_symbol_id(target_module),
                to_external=spec,
                file=rel, line=line, kind="import", lang="typescript",
                resolved=False, lang_meta={"reexport": True},
            ))

    # --- heritage refs ---------------------------------------------------------

    def _emit_heritage_ref(self, type_name, base_name, kind, node, ctx) -> None:
        """`class X extends Base` / `implements Iface` — resolve Base through
        the import-binding table, then same-file declarations; unknown bases
        stay unresolved externals (e.g. discord.js Client)."""
        root_name = base_name.split(".", 1)[0]
        binding = ctx.bindings.get(root_name)
        if binding is not None and binding.target_id:
            to_id = binding.target_id
        elif binding is not None:
            to_id = ""  # imported from an external package
        elif base_name in ctx.toplevel_names:
            to_id = ident.ts_decl_id(ctx.module_id, base_name)
        else:
            to_id = ""
        ctx.references.append(Reference(
            from_id=ident.ts_decl_id(ctx.module_id, type_name),
            to_id=to_id,
            to_external=base_name,
            file=ctx.rel,
            line=self._line(node),
            kind=kind,
            lang="typescript",
            resolved=False,
        ))

    def _heritage_target(self, extends_clause, src_bytes) -> str:
        """The expression a class extends: identifier or member chain text."""
        for c in extends_clause.children:
            if c.type == "identifier":
                return self._text(c, src_bytes)
            if c.type in ("member_expression", "generic_type", "nested_type_identifier"):
                return self._squash(self._text(c, src_bytes))
            if c.type == "type_identifier":
                return self._text(c, src_bytes)
        return ""

    def _named_type_text(self, node, src_bytes) -> str:
        if node.type in ("type_identifier", "identifier"):
            return self._text(node, src_bytes)
        if node.type == "generic_type":
            inner = next(
                (x for x in node.children if x.type in ("type_identifier", "nested_type_identifier")),
                None,
            )
            return self._text(inner, src_bytes) if inner is not None else ""
        if node.type == "nested_type_identifier":
            return self._squash(self._text(node, src_bytes))
        return ""

    # --- body walker --------------------------------------------------------------

    _TS_BUILTINS = frozenset({
        "console", "process", "globalThis", "window", "document",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "queueMicrotask", "structuredClone", "fetch", "require",
        "Promise", "Array", "Object", "String", "Number", "Boolean", "Symbol",
        "Date", "Math", "JSON", "RegExp", "Map", "Set", "WeakMap", "WeakSet",
        "Error", "TypeError", "RangeError", "SyntaxError", "AggregateError",
        "Buffer", "URL", "URLSearchParams", "TextEncoder", "TextDecoder",
        "AbortController", "AbortSignal", "Intl", "Proxy", "Reflect",
        "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
        "decodeURIComponent", "BigInt", "Atomics", "crypto",
        "super", "this",
    })

    def _walk_body(self, node, params: Set[str], ctx) -> None:
        locals_set: Set[str] = set(params) | set(ctx.toplevel_locals)
        try:
            self._walk_node(node, locals_set, ctx)
        except RecursionError:
            print(
                f"Warning: TypeScriptParser body walker hit RecursionError on "
                f"{ctx.rel}; skipping in-body reference extraction for this body.",
                file=sys.stderr,
            )

    def _walk_node(self, node, locals_set: Set[str], ctx) -> None:
        t = node.type
        if t == "variable_declarator":
            n = next((c for c in node.children if c.type == "identifier"), None)
            if n is not None:
                locals_set.add(self._text(n, ctx.src))
        elif t == "call_expression":
            self._handle_call_expr(node, locals_set, ctx)
        elif t == "new_expression":
            self._handle_new_expr(node, locals_set, ctx)
        for c in node.children:
            self._walk_node(c, locals_set, ctx)

    def _ref_target_for_name(self, name: str, locals_set: Set[str], ctx) -> str:
        """Map a root identifier to a predicted target id, or "" to skip."""
        if name in locals_set or name in self._TS_BUILTINS:
            return ""
        binding = ctx.bindings.get(name)
        if binding is not None:
            return binding.target_id  # "" for external packages -> skip
        if name in ctx.toplevel_names:
            return ident.ts_decl_id(ctx.module_id, name)
        return ""

    def _handle_call_expr(self, node, locals_set: Set[str], ctx) -> None:
        callee = node.children[0] if node.children else None
        if callee is None:
            return
        if callee.type == "identifier":
            name = self._text(callee, ctx.src)
            target = self._ref_target_for_name(name, locals_set, ctx)
            if not target:
                return
            ctx.references.append(Reference(
                from_id=ctx.from_id, to_id=target, to_external=name,
                file=ctx.rel, line=self._line(node), kind="call",
                lang="typescript", resolved=False,
            ))
        elif callee.type == "member_expression":
            obj = callee.children[0] if callee.children else None
            if obj is None or obj.type != "identifier":
                return  # nested chains (`a.b.c()`) — out of Phase 1 scope
            name = self._text(obj, ctx.src)
            target = self._ref_target_for_name(name, locals_set, ctx)
            if not target:
                return
            prop = next(
                (c for c in callee.children if c.type == "property_identifier"), None,
            )
            prop_text = self._text(prop, ctx.src) if prop is not None else ""
            ctx.references.append(Reference(
                from_id=ctx.from_id, to_id=target,
                to_external=f"{name}.{prop_text}" if prop_text else name,
                file=ctx.rel, line=self._line(node), kind="call",
                lang="typescript", resolved=False,
            ))

    def _handle_new_expr(self, node, locals_set: Set[str], ctx) -> None:
        ctor = next(
            (c for c in node.children if c.type in ("identifier", "member_expression")),
            None,
        )
        if ctor is None:
            return
        if ctor.type == "member_expression":
            obj = ctor.children[0] if ctor.children else None
            if obj is None or obj.type != "identifier":
                return
            name = self._text(obj, ctx.src)
            external = self._squash(self._text(ctor, ctx.src))
        else:
            name = self._text(ctor, ctx.src)
            external = name
        target = self._ref_target_for_name(name, locals_set, ctx)
        if not target:
            return
        ctx.references.append(Reference(
            from_id=ctx.from_id, to_id=target, to_external=external,
            file=ctx.rel, line=self._line(node), kind="uses",
            lang="typescript", resolved=False,
        ))

    # --- regex fallback --------------------------------------------------------

    def _parse_with_regex(
        self, content: str, rel: str, module_id: str, symbols: List[Symbol],
    ) -> Tuple[List[Symbol], List[Reference]]:
        """Conservative regex fallback when tree-sitter-typescript is missing.

        Captures top-level classes / interfaces / functions / enums / type
        aliases / arrow consts plus import statements as module-level refs.
        Methods, heritage, and in-body calls are NOT parsed in regex mode.
        """
        references: List[Reference] = []
        from_id = ident.ts_module_symbol_id(module_id)

        scan = re.sub(r"//[^\n]*", "", content)
        scan = re.sub(r"/\*[\s\S]*?\*/", "", scan)

        def _emit(kind: str, name: str, m: re.Match, signature: str, **meta) -> None:
            line = scan[: m.start()].count("\n") + 1
            symbols.append(Symbol(
                id=ident.ts_decl_id(module_id, name),
                name=name,
                fqn=f"{module_id}.{name}",
                kind=kind,
                file=rel, line=line,
                signature=signature,
                container=from_id,
                lang="typescript",
                lang_meta={"exported": bool(m.group(1)), **meta},
            ))

        for m in re.finditer(
            r"^\s*(export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
            scan, re.MULTILINE,
        ):
            _emit("class", m.group(2), m, f"class {m.group(2)}")
        for m in re.finditer(
            r"^\s*(export\s+)?interface\s+([A-Za-z_$][\w$]*)", scan, re.MULTILINE,
        ):
            _emit("interface", m.group(2), m, f"interface {m.group(2)}")
        for m in re.finditer(
            r"^\s*(export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)",
            scan, re.MULTILINE,
        ):
            _emit("function", m.group(2), m, f"function {m.group(2)}(...)")
        for m in re.finditer(
            r"^\s*(export\s+)?type\s+([A-Za-z_$][\w$]*)", scan, re.MULTILINE,
        ):
            _emit("interface", m.group(2), m, f"type {m.group(2)}",
                  declaration_form="type_alias")
        for m in re.finditer(
            r"^\s*(export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)", scan, re.MULTILINE,
        ):
            _emit("class", m.group(2), m, f"enum {m.group(2)}",
                  declaration_form="enum")
        for m in re.finditer(
            r"^\s*(export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\(",
            scan, re.MULTILINE,
        ):
            _emit("function", m.group(2), m, f"const {m.group(2)} = (...) =>",
                  arrow_const=True)

        for m in re.finditer(
            r"""^\s*(?:import|export)\s+[^;'"]*?from\s+['"]([^'"]+)['"]"""
            r"""|^\s*import\s+['"]([^'"]+)['"]""",
            scan, re.MULTILINE,
        ):
            spec = m.group(1) or m.group(2)
            line = scan[: m.start()].count("\n") + 1
            if spec.startswith("."):
                body = spec
                for ext in _STRIP_IMPORT_EXTS:
                    if body.endswith(ext):
                        body = body[: -len(ext)]
                        break
                # Path-normalize against the module's directory (regex mode
                # has no file-system access through this code path's inputs,
                # so `index.ts` directory imports stay best-effort).
                base_parts = module_id.split("/")[:-1]
                for seg in body.split("/"):
                    if seg in ("", "."):
                        continue
                    if seg == "..":
                        if base_parts:
                            base_parts.pop()
                    else:
                        base_parts.append(seg)
                guess = "/".join(base_parts)
                references.append(Reference(
                    from_id=from_id,
                    to_id=ident.ts_module_symbol_id(guess) if guess else "",
                    to_external=spec,
                    file=rel, line=line, kind="import", lang="typescript",
                    resolved=False,
                ))
            else:
                references.append(Reference(
                    from_id=from_id, to_id="", to_external=spec,
                    file=rel, line=line, kind="import", lang="typescript",
                    resolved=False,
                ))

        return symbols, references

    # --- AST helpers -----------------------------------------------------------

    @staticmethod
    def _text(node, src_bytes: bytes) -> str:
        try:
            return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _line(node) -> int:
        return node.start_point[0] + 1

    @staticmethod
    def _squash(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _truncate(text: str, limit: int = 120) -> str:
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def _string_text(self, node, src_bytes: bytes) -> str:
        text = self._text(node, src_bytes)
        if len(text) >= 2 and text[0] in "'\"`" and text[-1] == text[0]:
            return text[1:-1]
        return text

    def _type_params(self, node, src_bytes) -> str:
        tp = next((c for c in node.children if c.type == "type_parameters"), None)
        return self._squash(self._text(tp, src_bytes)) if tp is not None else ""

    def _member_modifiers(self, node, src_bytes) -> str:
        """Leading modifier text for a method signature: `private static async `."""
        mods: List[str] = []
        for c in node.children:
            if c.type == "accessibility_modifier":
                mods.append(self._text(c, src_bytes))
            elif c.type in ("static", "async", "abstract", "readonly", "get", "set", "override"):
                mods.append(c.type)
            elif c.type in ("property_identifier", "private_property_identifier"):
                break
        return " ".join(mods) + (" " if mods else "")

    def _callable_signature(self, name: str, node, src_bytes, *, prefix: str) -> str:
        """`<prefix> name(params): ret` from the raw parameter / return-type
        text, whitespace-squashed and truncated. Real types in L2 are the
        whole point of the signatures layer."""
        params = next((c for c in node.children if c.type == "formal_parameters"), None)
        ret = next((c for c in node.children if c.type == "type_annotation"), None)
        ptext = self._squash(self._text(params, src_bytes)) if params is not None else "(...)"
        rtext = self._squash(self._text(ret, src_bytes)) if ret is not None else ""
        lead = prefix if prefix.endswith(" ") or not prefix else prefix + " "
        return self._truncate(f"{lead}{name}{ptext}{rtext}")

    def _collect_param_names(self, node, src_bytes) -> Set[str]:
        """Identifier names bound by a callable's parameters (incl. inside
        destructuring patterns), used to suppress local-call false positives."""
        params = next((c for c in node.children if c.type == "formal_parameters"), None)
        names: Set[str] = set()
        if params is None:
            # single-parameter arrow without parens: `x => ...`
            first = node.children[0] if node.children else None
            if first is not None and first.type == "identifier":
                names.add(self._text(first, src_bytes))
            return names

        def collect(n) -> None:
            if n.type in ("identifier", "shorthand_property_identifier_pattern"):
                names.add(self._text(n, src_bytes))
                return
            for c in n.children:
                collect(c)

        collect(params)
        return names


@dataclass
class _WalkContext:
    """Per-file immutable context threaded through emission and body walks."""
    src: bytes
    rel: str
    module_id: str
    from_id: str
    bindings: Dict[str, _ImportBinding]
    toplevel_names: Set[str]
    toplevel_locals: Set[str]
    references: List[Reference]


__all__ = ["TypeScriptParser"]
