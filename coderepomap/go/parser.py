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

    # --- entry --------------------------------------------------------------

    def parse_file(self, file_path: Path, base_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        raise NotImplementedError("GoParser.parse_file: implemented in later tasks")


__all__ = ["GoParser"]
