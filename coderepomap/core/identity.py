"""
Stable Symbol.id generation for each supported language.

Rules
-----

C#:

    class      csharp:{namespace}.{type}
    method     csharp:{namespace}.{type}.{method}({param_types_csv})
    property   csharp:{namespace}.{type}.{property}
    field      csharp:{namespace}.{type}.{field}

- Empty namespace produces no leading dot.
- Nested types use dot-joined enclosing path: `Outer.Inner`.
- `param_types_csv` is the comma-joined raw type strings (no spaces).
- Empty params -> empty parens: `M()`. arity alone is not enough — overloads
  with the same arity but different parameter types must be distinguishable.
- Generic method parameters retain their textual form (e.g. `M(List<int>)`).

Lua:

    module    lua:{module_id}
    function  lua:{module_id}.{name}                    # top-level function
    function  lua:{module_id}.{table}.{name}            # function T.f() (no self)
    method    lua:{module_id}.{table}#{name}            # function T:m() (instance, implicit self)
    field     lua:{module_id}.{table}.{field}

- The `lua:` prefix appears at most once at the very start.
- After the prefix, `:` is forbidden in the body (use `#` for instance methods).
- `module_id` is the source-root-relative path with `/` -> `.` and `.lua` stripped
  (e.g. `Assets/LuaScripts/foo/bar.lua` -> `foo.bar`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


# ----- C# -----

def _csharp_dot_join(*parts: str) -> str:
    """Join non-empty parts with `.`. Empty parts are skipped."""
    return ".".join(p for p in parts if p)


def csharp_type_id(namespace: str, enclosing: str, name: str) -> str:
    """Stable id for a C# class / struct / interface / enum.

    `enclosing` is the dot-joined outer-type path (empty for top-level types,
    `Outer` for `Outer.Inner`, `Outer.Mid` for `Outer.Mid.Inner`, ...).
    """
    body = _csharp_dot_join(namespace, enclosing, name)
    return f"csharp:{body}"


def csharp_method_id(
    namespace: str,
    enclosing_type: str,
    method: str,
    param_types: Iterable[str],
) -> str:
    """Stable id for a C# method, distinguishing overloads by parameter types.

    `enclosing_type` is the full dot-joined type path (including outer types
    for nested classes). `param_types` is the ordered list of textual type
    expressions extracted by the parser. Whitespace inside each type string
    is preserved as-is; the joiner is `,` with no spaces.
    """
    types_csv = ",".join(param_types)
    body = _csharp_dot_join(namespace, enclosing_type, method)
    return f"csharp:{body}({types_csv})"


def csharp_member_id(namespace: str, enclosing_type: str, member: str) -> str:
    """Stable id for a C# property / field / event (anything name-unique within type)."""
    body = _csharp_dot_join(namespace, enclosing_type, member)
    return f"csharp:{body}"


# ----- Lua -----

def lua_module_id_from_path(file_path: Path, source_root: Path) -> str:
    """Compute `module_id` from a `.lua` file path relative to a source root.

    `Assets/LuaScripts/foo/bar.lua` with root `Assets/LuaScripts`
    -> `foo.bar`.
    """
    rel = file_path.resolve().relative_to(source_root.resolve())
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts)


def lua_module_symbol_id(module_id: str) -> str:
    """Id for the module-as-symbol (one per file)."""
    return f"lua:{module_id}"


def lua_function_id(module_id: str, name: str, table: str = "") -> str:
    """Id for `function name()` or `function T.f()` (no implicit self).

    `table=""` means top-level function in the module. Anything else means
    `table.name` — a "static-like" call (`T.f(arg)`).
    """
    if table:
        body = f"{module_id}.{table}.{name}"
    else:
        body = f"{module_id}.{name}"
    return f"lua:{body}"


def lua_method_id(module_id: str, table: str, name: str) -> str:
    """Id for `function T:m()` (implicit self). Uses `#` separator.

    Different from `lua_function_id(module_id, name, table)` which uses `.`.
    Two functions with the same `table.name` but different colon vs. dot
    declarations are intentionally treated as different symbols — they ARE
    different (one binds `self`, the other doesn't).
    """
    return f"lua:{module_id}.{table}#{name}"


def lua_field_id(module_id: str, table: str, field: str) -> str:
    """Id for `T.x = ...` assignments at module scope."""
    return f"lua:{module_id}.{table}.{field}"


# ----- Go -----

def _go_body(module_path: str, rel_dir: str) -> str:
    """Join module-path + rel-dir, normalize separators, tolerate empties."""
    rel = rel_dir.replace("\\", "/").strip("/")
    mp = module_path.strip("/")
    if mp and rel:
        return f"{mp}/{rel}"
    return mp or rel


def go_package_id(module_path: str, rel_dir: str) -> str:
    """Id for a Go package symbol (one per directory).

    `module_path` comes from `go.mod`; empty means no module declared, the id
    falls back to the source-root-relative directory. `rel_dir` is normalized
    to forward slashes so Windows paths produce stable ids.
    """
    body = _go_body(module_path, rel_dir)
    return f"go:{body}"


def go_function_id(module_path: str, rel_dir: str, name: str) -> str:
    """Id for a Go top-level function: `<pkg-id>.<Name>`."""
    return f"{go_package_id(module_path, rel_dir)}.{name}"


def go_type_id(module_path: str, rel_dir: str, name: str) -> str:
    """Id for a Go named type (struct / interface / alias): `<pkg-id>.<Type>`."""
    return f"{go_package_id(module_path, rel_dir)}.{name}"


def go_method_id(module_path: str, rel_dir: str, type_name: str, method: str) -> str:
    """Id for a Go method. Pointer/value receivers produce the same id; the
    distinction is recorded in `Symbol.lang_meta.receiver_kind`."""
    return f"{go_package_id(module_path, rel_dir)}.{type_name}.{method}"


def go_member_id(module_path: str, rel_dir: str, type_name: str, member: str) -> str:
    """Id for a Go struct field or top-level const/var on a type."""
    return f"{go_package_id(module_path, rel_dir)}.{type_name}.{member}"


__all__ = [
    "csharp_type_id",
    "csharp_method_id",
    "csharp_member_id",
    "lua_module_id_from_path",
    "lua_module_symbol_id",
    "lua_function_id",
    "lua_method_id",
    "lua_field_id",
    "go_package_id",
    "go_function_id",
    "go_type_id",
    "go_method_id",
    "go_member_id",
]
