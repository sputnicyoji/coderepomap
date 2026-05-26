"""
Cross-language reference resolver.

Two roles:

1. **Post-resolve Lua-internal references** that LuaParser couldn't verify
   locally — e.g. `require "foo.bar"` produces `to_id="lua:foo.bar"` with
   `resolved=False`. If a `Symbol(kind='module', id='lua:foo.bar')` exists
   in the combined symbol set, flip `resolved=True`.

2. **Resolve Lua -> C# `csharp_call` references** by mapping the textual
   chain (e.g. `CS.UnityEngine.GameObject.Find`) to a real C# Symbol.id.
   Three outcomes:
   - exact FQN match -> resolved
   - short-name unique match -> resolved
   - short-name multi-match -> unresolved with `lang_meta["candidates"]`

Pure function; no I/O, no parser knowledge. Inputs are Symbol / Reference
lists; mutates the Reference list in place.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .parser_base import Reference, Symbol


_CSHARP_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum"})


def _build_indexes(symbols: List[Symbol]) -> Dict[str, object]:
    """Build the lookup indexes used by resolve().

    csharp_fqn_index / csharp_short_index only contain TYPE symbols. Lua->C#
    calls of the form `CS.NS.Type.Method` resolve to the enclosing type
    (Method symbols carry param-type signatures in their id which the Lua
    side can't possibly know). For type-only references like `CS.NS.Type`
    they resolve exactly.
    """
    id_index: Dict[str, Symbol] = {}
    csharp_fqn_index: Dict[str, Symbol] = {}
    csharp_short_index: Dict[str, List[Symbol]] = defaultdict(list)

    for s in symbols:
        id_index[s.id] = s
        if s.lang == "csharp" and s.kind in _CSHARP_TYPE_KINDS:
            csharp_fqn_index[s.fqn] = s
            csharp_short_index[s.name].append(s)

    return {
        "id_index": id_index,
        "csharp_fqn_index": csharp_fqn_index,
        "csharp_short_index": csharp_short_index,
    }


def _strip_csharp_prefixes(chain: str, prefixes: List[str]) -> str:
    """Drop the configured Lua-side prefix (e.g. `CS.` for xLua) from the chain."""
    for p in prefixes:
        norm = p.rstrip(".")
        if chain == norm:
            return ""
        prefix_dot = norm + "."
        if chain.startswith(prefix_dot):
            return chain[len(prefix_dot):]
    return chain


def resolve(
    symbols: List[Symbol],
    references: List[Reference],
    config: dict,
) -> None:
    """In-place: flip resolved=True / set to_id when possible.

    Idempotent.
    """
    indexes = _build_indexes(symbols)
    id_index: Dict[str, Symbol] = indexes["id_index"]  # type: ignore[assignment]
    csharp_fqn_index: Dict[str, Symbol] = indexes["csharp_fqn_index"]  # type: ignore[assignment]
    csharp_short_index: Dict[str, List[Symbol]] = indexes["csharp_short_index"]  # type: ignore[assignment]

    crosslang_cfg = config.get("crosslang", {}) or {}
    enabled = crosslang_cfg.get("enabled", True)
    lua_cs_patterns = crosslang_cfg.get("lua_csharp_call_patterns", []) or []
    prefixes = [p.get("prefix", "") for p in lua_cs_patterns if p.get("prefix")]
    if not prefixes:
        prefixes = ["CS."]  # xLua default

    # Build a short-name index for C# resolution that covers TYPES across
    # the whole project (needed for cross-file `inherits` / `implements`
    # references — LegacyCSharpParser emits refs by bare type name, so we
    # need a project-wide lookup to upgrade `to_id=""` cross-file edges).
    for ref in references:
        if ref.resolved and ref.to_id:
            continue

        # Path 1: Lua-internal refs that already have a candidate to_id from the parser.
        if ref.to_id and ref.to_id in id_index:
            ref.resolved = True
            ref.to_external = ""  # we now have a real id; drop the textual form
            continue

        # Path 1b: C# cross-file `inherits` / `implements` references.
        # CSharpParser's _project_references only knows the current file,
        # so a Child in file_a.cs that inherits Base in file_b.cs comes
        # through with to_id="" and to_external="Base" (the bare type name).
        # Use the project-wide short-name index to resolve.
        if (
            ref.lang == "csharp"
            and ref.kind in ("inherits", "implements")
            and not ref.to_id
            and ref.to_external
        ):
            candidates = csharp_short_index.get(ref.to_external, [])
            if len(candidates) == 1:
                ref.to_id = candidates[0].id
                ref.resolved = True
                continue
            # Multiple candidates: leave unresolved with candidates list so
            # the renderer can show ambiguity (same handling as csharp_call).
            if len(candidates) > 1:
                ref.lang_meta = {**ref.lang_meta, "candidates": [c.id for c in candidates]}
                continue
            # 0 candidates: external base (e.g. UnityEngine.MonoBehaviour
            # not in scanned sources). Leave as unresolved external.
            continue

        # Path 2: Lua-internal refs with `to_id` like `lua:module.foo` where
        # `module` exists but the trailing `.foo` doesn't. Promote to
        # module-level resolve for BOTH `require` AND `call`. For call,
        # resolving to the module gives the graph an edge to the module
        # symbol — coarser than resolving to the function, but graph-correct
        # and far better than dropping the edge entirely.
        if ref.to_id.startswith("lua:") and ref.kind in ("call", "require"):
            body = ref.to_id[len("lua:"):]
            parts = body.split(".")
            for i in range(len(parts), 0, -1):
                candidate = "lua:" + ".".join(parts[:i])
                if candidate in id_index:
                    ref.to_id = candidate
                    ref.resolved = True
                    ref.to_external = body if ref.kind == "call" and i < len(parts) else ""
                    break

        # Path 3: csharp_call from Lua, target lives as textual chain in to_external.
        if not enabled:
            continue
        if ref.kind != "csharp_call":
            continue
        if ref.resolved and ref.to_id:
            continue
        chain = ref.to_external
        if not chain:
            continue
        # Drop the Lua-side prefix (CS. / UnityEngine. / etc.) before fqn match.
        stripped = _strip_csharp_prefixes(chain, prefixes)
        if not stripped:
            continue

        # 3a. Exact FQN match
        sym = csharp_fqn_index.get(stripped)
        if sym is None:
            # Try dropping the trailing `.method` to match an outer type
            parts = stripped.split(".")
            for i in range(len(parts) - 1, 0, -1):
                candidate = ".".join(parts[:i])
                m = csharp_fqn_index.get(candidate)
                if m is not None:
                    sym = m
                    break
        if sym is not None:
            ref.to_id = sym.id
            ref.resolved = True
            ref.to_external = chain  # keep original for L3 audit
            continue

        # 3b. Short-name match (the last segment after the stripped prefix)
        last = stripped.rsplit(".", 1)[-1] if "." in stripped else stripped
        candidates = csharp_short_index.get(last, [])
        if len(candidates) == 1:
            ref.to_id = candidates[0].id
            ref.resolved = True
            ref.to_external = chain
        elif len(candidates) > 1:
            # Ambiguous: leave unresolved, record candidates
            ref.lang_meta = {**ref.lang_meta, "candidates": [c.id for c in candidates]}
            # Stay resolved=False, to_id=""


__all__ = ["resolve"]
