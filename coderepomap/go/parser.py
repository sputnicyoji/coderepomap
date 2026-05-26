#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Go parser plug-in for coderepomap.

Targets generic Go modules: one package per directory, struct/interface/method
analysis via tree-sitter-go, file-local import alias table, optimistic cross-
file id prediction for crosslang post-resolve.

Phase 1 scope:
- Symbol: package / function / class (struct + alias) / interface / method / field
- Reference: import / call / uses / inherits (struct + interface embedding)
- Tree-sitter primary path, regex fallback
- go.mod-based module path; relative-dir fallback when go.mod is absent
- No cross-language analysis (Go <-> C# / Lua) in Phase 1
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core import identity as ident
from ..core.parser_base import LanguageParser, Reference, Symbol


@dataclass
class _ImportEntry:
    """One file-local import binding: selector -> package id + textual path."""
    selector: str    # how this package is referenced in code (`svc`, `service`, `.`, `_`)
    path: str        # original import path string
    package_id: str  # the predicted `go:<path>` id
    confidence: str = "high"  # "high" when explicit alias; "low" when basename heuristic
    is_blank: bool = False
    is_dot: bool = False


class GoParser(LanguageParser):
    lang = "go"
    file_extensions = [".go"]
    graph_node_kinds = ["package", "class", "interface", "function"]
    default_excludes = [
        "**/vendor/**",
        "**/testdata/**",
        "**/*_test.go",
        "**/.git/**",
        "**/node_modules/**",
        "**/bin/**",
    ]
    default_boost_patterns = [
        {"suffix": "er",         "boost": 1.3, "description": "Interfaces (Go -er convention)"},
        {"suffix": "Service",    "boost": 1.8, "description": "Service layer"},
        {"suffix": "Handler",    "boost": 1.4, "description": "HTTP/event handlers"},
        {"suffix": "Server",     "boost": 1.5, "description": "Server components"},
        {"suffix": "Client",     "boost": 1.5, "description": "Client wrappers"},
        {"suffix": "Manager",    "boost": 1.5, "description": "Manager"},
        {"suffix": "Repository", "boost": 1.5, "description": "Data access"},
        {"suffix": "Store",      "boost": 1.4, "description": "Storage abstractions"},
    ]
    default_categories = {
        "Cmd":      {"patterns": ["cmd", "main"]},
        "Internal": {"patterns": ["internal"]},
        "Pkg":      {"patterns": ["pkg"]},
        "API":      {"patterns": ["api", "handler", "server", "route"]},
        "Domain":   {"patterns": ["model", "entity", "domain"]},
        "Storage":  {"patterns": ["repo", "repository", "store", "db"]},
        "Other":    {"patterns": []},
    }

    def __init__(self):
        self._parser = None
        self._language = None
        self._initialized = False
        self._module_path_cache: Dict[Path, str] = {}
        self._parsed_packages: set = set()
        self._import_tables: Dict[str, List[_ImportEntry]] = {}

    # --- TS init ---------------------------------------------------------------

    def _init_parser(self) -> bool:
        if self._initialized:
            return self._parser is not None
        try:
            import tree_sitter_go as ts_go
            from tree_sitter import Language, Parser

            self._language = Language(ts_go.language())
            self._parser = Parser(self._language)
            self._initialized = True
            return True
        except ImportError as e:
            print(f"Warning: tree-sitter-go not available: {e}", file=sys.stderr)
            print("Falling back to regex-based Go parsing", file=sys.stderr)
            self._initialized = True
            return False
        except Exception as e:
            print(f"Warning: tree-sitter-go init failed: {e}", file=sys.stderr)
            self._initialized = True
            return False

    # --- go.mod resolution -------------------------------------------------

    _MOD_RE = re.compile(r"^module\s+([^\s]+)", re.MULTILINE)

    def _resolve_module_path(self, base_path: Path) -> str:
        """Find `go.mod` at base_path (and ancestors up to filesystem root)."""
        cached = self._module_path_cache.get(base_path)
        if cached is not None:
            return cached
        cur = base_path.resolve()
        path = ""
        for candidate in [cur, *cur.parents]:
            mod = candidate / "go.mod"
            if mod.is_file():
                try:
                    text = mod.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                m = self._MOD_RE.search(text)
                if m:
                    path = m.group(1).strip()
                break
        self._module_path_cache[base_path] = path
        return path

    @staticmethod
    def _rel_dir_from_file(file_path: Path, base_path: Path) -> str:
        try:
            rel = file_path.resolve().relative_to(base_path.resolve())
        except ValueError:
            return file_path.parent.as_posix()
        rel_parent = rel.parent
        if str(rel_parent) in (".", ""):
            return ""
        return rel_parent.as_posix()

    # --- entry --------------------------------------------------------------

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

        module_path = self._resolve_module_path(base_path)
        rel_dir = self._rel_dir_from_file(file_path, base_path)
        package_id = ident.go_package_id(module_path, rel_dir)

        if self._init_parser():
            return self._parse_with_ts(content, rel, module_path, rel_dir, package_id)
        return self._parse_with_regex(content, rel, module_path, rel_dir, package_id)

    # --- TS walker (filled in by later tasks) -------------------------------

    def _parse_with_ts(
        self, content: str, rel: str, module_path: str, rel_dir: str, package_id: str,
    ) -> Tuple[List[Symbol], List[Reference]]:
        symbols: List[Symbol] = []
        references: List[Reference] = []

        content_bytes = content.encode("utf-8")
        try:
            tree = self._parser.parse(content_bytes)
        except Exception as e:
            print(
                f"Warning: tree-sitter-go failed on {rel}: {type(e).__name__}: {e}. "
                f"Falling back to regex.", file=sys.stderr,
            )
            return self._parse_with_regex(content, rel, module_path, rel_dir, package_id)

        root = tree.root_node
        package_name = ""
        for child in root.children:
            if child.type == "package_clause":
                for c in child.children:
                    if c.type == "package_identifier":
                        package_name = self._text(c, content_bytes)
                        break
                break

        if package_id not in self._parsed_packages:
            self._parsed_packages.add(package_id)
            display_name = package_name or (
                package_id.rsplit("/", 1)[-1] if "/" in package_id
                else package_id[len("go:"):]
            )
            symbols.append(Symbol(
                id=package_id,
                name=display_name,
                fqn=package_id[len("go:"):],
                kind="package",
                file=rel,
                line=1,
                lang="go",
                lang_meta={
                    "package_name": package_name,
                    "import_path": package_id[len("go:"):],
                },
            ))

        for child in root.children:
            if child.type == "function_declaration":
                self._handle_function_decl(
                    child, content_bytes, rel, module_path, rel_dir, symbols, references,
                )
            elif child.type == "type_declaration":
                self._handle_type_decl(
                    child, content_bytes, rel, module_path, rel_dir, symbols, references,
                )
            elif child.type == "method_declaration":
                self._handle_method_decl(
                    child, content_bytes, rel, module_path, rel_dir, symbols, references,
                )

        return symbols, references

    def _handle_method_decl(
        self, node, src_bytes, rel, module_path, rel_dir, symbols, references,
    ):
        receiver_node = next((c for c in node.children if c.type == "parameter_list"), None)
        if receiver_node is None:
            return
        recv_decl = next(
            (c for c in receiver_node.children if c.type == "parameter_declaration"),
            None,
        )
        if recv_decl is None:
            return
        recv_kind = "val"
        recv_type = ""
        for c in recv_decl.children:
            if c.type == "pointer_type":
                recv_kind = "ptr"
                inner = next(
                    (x for x in c.children if x.type == "type_identifier"),
                    None,
                )
                if inner is not None:
                    recv_type = self._text(inner, src_bytes)
            elif c.type == "type_identifier":
                recv_type = self._text(c, src_bytes)
        if not recv_type:
            return

        name_node = next((c for c in node.children if c.type == "field_identifier"), None)
        if name_node is None:
            return
        name = self._text(name_node, src_bytes)
        symbols.append(Symbol(
            id=ident.go_method_id(module_path, rel_dir, recv_type, name),
            name=name,
            fqn=ident.go_method_id(module_path, rel_dir, recv_type, name)[len("go:"):],
            kind="method",
            file=rel,
            line=self._line(node),
            container=ident.go_package_id(module_path, rel_dir),
            parent=recv_type,
            lang="go",
            lang_meta={
                "exported": self._is_exported(name),
                "receiver_kind": recv_kind,
            },
        ))

    def _handle_type_decl(
        self, node, src_bytes, rel, module_path, rel_dir, symbols, references,
    ):
        for spec in node.children:
            if spec.type != "type_spec":
                continue
            name_node = next((c for c in spec.children if c.type == "type_identifier"), None)
            if name_node is None:
                continue
            name = self._text(name_node, src_bytes)
            line = self._line(spec)

            type_params: List[str] = []
            for c in spec.children:
                if c.type == "type_parameter_list":
                    for tp in c.children:
                        if tp.type == "type_parameter_declaration":
                            ident_node = next((x for x in tp.children if x.type == "identifier"), None)
                            if ident_node is not None:
                                type_params.append(self._text(ident_node, src_bytes))

            # The first type_identifier child IS the type name (name_node above).
            # Skip it when scanning for the inner type expression.
            inner = None
            for c in spec.children:
                if c is name_node:
                    continue
                if c.type in ("struct_type", "interface_type"):
                    inner = c
                    break
                if c.type in ("type_identifier", "pointer_type", "slice_type", "map_type",
                              "array_type", "function_type", "channel_type", "qualified_type"):
                    inner = c
                    break

            kind = "interface" if (inner is not None and inner.type == "interface_type") else "class"

            symbols.append(Symbol(
                id=ident.go_type_id(module_path, rel_dir, name),
                name=name,
                fqn=ident.go_type_id(module_path, rel_dir, name)[len("go:"):],
                kind=kind,
                file=rel,
                line=line,
                container=ident.go_package_id(module_path, rel_dir),
                lang="go",
                lang_meta={
                    "exported": self._is_exported(name),
                    "generic_params": type_params,
                },
            ))

            if inner is not None and inner.type == "struct_type":
                self._emit_struct_fields(
                    inner, src_bytes, rel, module_path, rel_dir, name, symbols, references,
                )

    def _emit_struct_fields(
        self, struct_node, src_bytes, rel, module_path, rel_dir, type_name,
        symbols, references,
    ):
        body = next((c for c in struct_node.children if c.type == "field_declaration_list"), None)
        if body is None:
            return
        for decl in body.children:
            if decl.type != "field_declaration":
                continue
            names = [c for c in decl.children if c.type == "field_identifier"]
            if names:
                for n in names:
                    fname = self._text(n, src_bytes)
                    symbols.append(Symbol(
                        id=ident.go_member_id(module_path, rel_dir, type_name, fname),
                        name=fname,
                        fqn=ident.go_member_id(module_path, rel_dir, type_name, fname)[len("go:"):],
                        kind="field",
                        file=rel,
                        line=self._line(n),
                        container=ident.go_package_id(module_path, rel_dir),
                        parent=type_name,
                        lang="go",
                        lang_meta={"exported": self._is_exported(fname)},
                    ))
            else:
                # Embedded field: `Result` (same package) or `pkg.Type`
                # (cross-package via import alias resolution in Task 13).
                embed_id = None
                embed_external = ""
                for c in decl.children:
                    if c.type == "type_identifier":
                        ename = self._text(c, src_bytes)
                        embed_id = ident.go_type_id(module_path, rel_dir, ename)
                        embed_external = ename
                        break
                    if c.type == "qualified_type":
                        pkg_node = next((x for x in c.children if x.type == "package_identifier"), None)
                        tname_node = next((x for x in c.children if x.type == "type_identifier"), None)
                        if pkg_node is not None and tname_node is not None:
                            embed_external = f"{self._text(pkg_node, src_bytes)}.{self._text(tname_node, src_bytes)}"
                            # Cross-package id resolution is filled in by Task 13
                            # once the import table exists.
                            embed_id = ""
                        break
                if embed_id is not None:
                    references.append(Reference(
                        from_id=ident.go_type_id(module_path, rel_dir, type_name),
                        to_id=embed_id,
                        to_external=embed_external,
                        file=rel,
                        line=self._line(decl),
                        kind="inherits",
                        lang="go",
                        resolved=False,
                    ))

    def _handle_function_decl(
        self, node, src_bytes, rel, module_path, rel_dir, symbols, references,
    ):
        name_node = None
        for c in node.children:
            if c.type == "identifier":
                name_node = c
                break
        if name_node is None:
            return
        name = self._text(name_node, src_bytes)
        line = self._line(node)

        type_params: List[str] = []
        for c in node.children:
            if c.type == "type_parameter_list":
                for tp in c.children:
                    if tp.type == "type_parameter_declaration":
                        ident_node = next((x for x in tp.children if x.type == "identifier"), None)
                        if ident_node is not None:
                            type_params.append(self._text(ident_node, src_bytes))

        symbols.append(Symbol(
            id=ident.go_function_id(module_path, rel_dir, name),
            name=name,
            fqn=ident.go_function_id(module_path, rel_dir, name)[len("go:"):],
            kind="function",
            file=rel,
            line=line,
            container=ident.go_package_id(module_path, rel_dir),
            lang="go",
            lang_meta={
                "exported": self._is_exported(name),
                "generic_params": type_params,
            },
        ))

    # --- regex fallback (filled in by Task 15) ------------------------------

    def _parse_with_regex(
        self, content: str, rel: str, module_path: str, rel_dir: str, package_id: str,
    ) -> Tuple[List[Symbol], List[Reference]]:
        return [], []

    # --- AST helpers --------------------------------------------------------

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
    def _is_exported(name: str) -> bool:
        return bool(name) and name[0].isupper()


__all__ = ["GoParser"]
