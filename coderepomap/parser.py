#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C# Parser using Tree-sitter

Extracts code structure from C# files:
- Namespaces, classes, methods, properties
- References (method calls, type usages)
- Inheritance relationships

Based on tree-sitter-c-sharp grammar.
"""

import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple
import re


@dataclass
class Symbol:
    """Represents a code symbol (class, method, property)"""
    name: str
    kind: str  # 'class', 'method', 'property', 'field'
    file: str
    line: int
    signature: str = ""
    namespace: str = ""
    parent_class: str = ""
    base_class: str = ""
    interfaces: List[str] = field(default_factory=list)
    modifiers: List[str] = field(default_factory=list)


@dataclass
class Reference:
    """Represents a reference from one symbol to another"""
    from_file: str
    from_symbol: str
    to_symbol: str
    ref_type: str  # 'inherits', 'implements', 'calls', 'uses'


class CSharpParser:
    """Parser for C# files using tree-sitter"""

    def __init__(self):
        self._parser = None
        self._language = None
        self._initialized = False

    def _init_parser(self):
        """Lazy initialization of tree-sitter parser"""
        if self._initialized:
            return True

        try:
            import tree_sitter_c_sharp as ts_csharp
            from tree_sitter import Language, Parser

            self._language = Language(ts_csharp.language())
            self._parser = Parser(self._language)
            self._initialized = True
            return True
        except ImportError as e:
            print(f"Warning: tree-sitter-c-sharp not available: {e}")
            print("Falling back to regex-based parsing")
            return False
        except Exception as e:
            print(f"Warning: Failed to initialize tree-sitter: {e}")
            return False

    def parse_file(self, file_path: Path, base_path: Path) -> Tuple[List[Symbol], List[Reference]]:
        """
        Parse a C# file and extract symbols and references.

        Args:
            file_path: Path to the .cs file
            base_path: Base path for relative file paths

        Returns:
            Tuple of (symbols list, references list)
        """
        content = None
        # Try multiple encodings
        for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']:
            try:
                content = file_path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if content is None:
            # Last resort: read as bytes and decode with errors ignored
            try:
                content = file_path.read_bytes().decode('utf-8', errors='ignore')
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
                return [], []

        relative_path = str(file_path.relative_to(base_path)).replace('\\', '/')

        # Try tree-sitter first, fall back to regex
        if self._init_parser():
            return self._parse_with_tree_sitter(content, relative_path)
        else:
            return self._parse_with_regex(content, relative_path)

    def _parse_with_tree_sitter(self, content: str, file_path: str) -> Tuple[List[Symbol], List[Reference]]:
        """Parse using tree-sitter AST"""
        symbols = []
        references = []

        # CRITICAL: Tree-sitter uses byte offsets, not character offsets!
        # We must use bytes for slicing, not the original string
        content_bytes = content.encode('utf-8')
        tree = self._parser.parse(content_bytes)
        root = tree.root_node

        # Extract namespace
        namespace = self._extract_namespace(root, content_bytes)

        # Find all class/struct/interface declarations
        self._extract_types(root, content_bytes, file_path, namespace, symbols, references)

        return symbols, references

    def _get_node_text(self, node, content_bytes: bytes) -> str:
        """
        Safely extract text from a tree-sitter node.

        This is the CORRECT way to get text - using bytes slicing then decode.
        Using content[start_byte:end_byte] on a str is WRONG and causes truncation.
        """
        try:
            text_bytes = content_bytes[node.start_byte:node.end_byte]
            return text_bytes.decode('utf-8', errors='replace')
        except Exception:
            return ""

    def _extract_namespace(self, node, content_bytes: bytes) -> str:
        """Extract namespace from AST"""
        for child in node.children:
            if child.type == 'namespace_declaration':
                for sub in child.children:
                    if sub.type == 'qualified_name' or sub.type == 'identifier':
                        return self._get_node_text(sub, content_bytes)
            elif child.type == 'file_scoped_namespace_declaration':
                for sub in child.children:
                    if sub.type == 'qualified_name' or sub.type == 'identifier':
                        return self._get_node_text(sub, content_bytes)
        return ""

    def _extract_types(self, node, content_bytes: bytes, file_path: str, namespace: str,
                       symbols: List[Symbol], references: List[Reference], parent_class: str = ""):
        """Recursively extract type declarations"""

        type_kinds = {
            'class_declaration': 'class',
            'struct_declaration': 'struct',
            'interface_declaration': 'interface',
            'enum_declaration': 'enum',
        }

        for child in node.children:
            if child.type in type_kinds:
                kind = type_kinds[child.type]
                name, base, interfaces, modifiers = self._extract_type_info(child, content_bytes)

                if name:
                    line = child.start_point[0] + 1
                    signature = self._build_type_signature(name, base, interfaces, modifiers, kind)

                    symbol = Symbol(
                        name=name,
                        kind=kind,
                        file=file_path,
                        line=line,
                        signature=signature,
                        namespace=namespace,
                        parent_class=parent_class,
                        base_class=base,
                        interfaces=interfaces,
                        modifiers=modifiers
                    )
                    symbols.append(symbol)

                    # Add inheritance references
                    if base:
                        references.append(Reference(
                            from_file=file_path,
                            from_symbol=name,
                            to_symbol=base,
                            ref_type='inherits'
                        ))
                    for iface in interfaces:
                        references.append(Reference(
                            from_file=file_path,
                            from_symbol=name,
                            to_symbol=iface,
                            ref_type='implements'
                        ))

                    # Extract methods from this type
                    self._extract_members(child, content_bytes, file_path, namespace, name, symbols, references)

                    # Recursively check for nested types
                    self._extract_types(child, content_bytes, file_path, namespace, symbols, references, name)

            elif child.type in ('namespace_declaration', 'file_scoped_namespace_declaration'):
                self._extract_types(child, content_bytes, file_path, namespace, symbols, references, parent_class)
            elif child.type == 'declaration_list':
                self._extract_types(child, content_bytes, file_path, namespace, symbols, references, parent_class)

    def _extract_type_info(self, node, content_bytes: bytes) -> Tuple[str, str, List[str], List[str]]:
        """Extract type name, base class, interfaces, and modifiers"""
        name = ""
        base = ""
        interfaces = []
        modifiers = []

        # C# keywords and common prefixes that are NOT valid class names
        INVALID_NAMES = {
            'class', 'struct', 'interface', 'enum', 'namespace', 'using',
            'public', 'private', 'protected', 'internal', 'abstract', 'sealed',
            'static', 'partial', 'readonly', 'virtual', 'override', 'new',
            'void', 'int', 'string', 'bool', 'float', 'double', 'object',
            'var', 'dynamic', 'async', 'await', 'return', 'if', 'else',
            'for', 'foreach', 'while', 'do', 'switch', 'case', 'break',
            'continue', 'throw', 'try', 'catch', 'finally', 'null', 'true', 'false'
        }

        for child in node.children:
            if child.type == 'identifier' and not name:
                # First identifier is the type name
                candidate = self._get_node_text(child, content_bytes)
                # Validate it's a proper class/type identifier
                if candidate and self._is_valid_type_name(candidate, INVALID_NAMES):
                    name = candidate
            elif child.type == 'modifier':
                mod = self._get_node_text(child, content_bytes)
                if mod in ('public', 'private', 'protected', 'internal', 'abstract',
                           'sealed', 'static', 'partial', 'readonly', 'virtual', 'override'):
                    modifiers.append(mod)
            elif child.type == 'base_list':
                bases = self._extract_base_list(child, content_bytes)
                for b in bases:
                    # Validate base name
                    if b and re.match(r'^[A-Za-z_][A-Za-z0-9_<>]*$', b):
                        if re.match(r'^I[A-Z]', b):
                            interfaces.append(b)
                        elif not base:
                            base = b

        return name, base, interfaces, modifiers

    def _is_valid_type_name(self, name: str, invalid_names: set) -> bool:
        """
        Validate if a string is a valid C# type name.
        Filters out:
        - Keywords and reserved words
        - Field names (starting with m_, s_, c_, or lowercase)
        - Truncated/partial identifiers (too short)
        - Names that look like partial words
        """
        if not name:
            return False

        # Must be valid identifier format
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            return False

        # Filter out keywords
        if name.lower() in invalid_names:
            return False

        # Filter out field naming patterns (m_XXX, s_XXX, c_XXX)
        if re.match(r'^[msc]_', name):
            return False

        # Class names should start with uppercase (or I for interface, or _ for special)
        # Filter out names starting with lowercase that look like variables
        if name[0].islower() and not name.startswith('_'):
            return False

        # Minimum length check - real class names are usually > 3 chars
        # Allow short names if they're all uppercase (like UI, IO, etc.)
        if len(name) < 4 and not name.isupper():
            return False

        # Filter out partial/truncated names - these often have unusual patterns
        # Real class names are usually PascalCase or have clear word boundaries
        # Truncated names often have lowercase-uppercase without pattern
        if len(name) > 3:
            # Check if it looks like a truncated word (no vowels often indicates truncation)
            vowels = set('aeiouAEIOU')
            if len(name) > 5 and not any(c in vowels for c in name):
                return False

        return True

    def _extract_base_list(self, node, content_bytes: bytes) -> List[str]:
        """Extract base types from base_list node"""
        bases = []
        for child in node.children:
            if child.type in ('identifier', 'qualified_name', 'generic_name'):
                text = self._get_node_text(child, content_bytes)
                text = re.sub(r'<[^>]+>', '<T>', text)
                bases.append(text)
            elif child.type == 'base_type':
                for sub in child.children:
                    if sub.type in ('identifier', 'qualified_name', 'generic_name'):
                        text = self._get_node_text(sub, content_bytes)
                        text = re.sub(r'<[^>]+>', '<T>', text)
                        bases.append(text)
        return bases

    def _extract_members(self, type_node, content_bytes: bytes, file_path: str, namespace: str,
                         class_name: str, symbols: List[Symbol], references: List[Reference]):
        """Extract methods and properties from a type"""

        for child in type_node.children:
            if child.type == 'declaration_list':
                for member in child.children:
                    if member.type == 'method_declaration':
                        self._extract_method(member, content_bytes, file_path, namespace, class_name, symbols, references)
                    elif member.type == 'property_declaration':
                        self._extract_property(member, content_bytes, file_path, namespace, class_name, symbols)

    def _extract_method(self, node, content_bytes: bytes, file_path: str, namespace: str,
                        class_name: str, symbols: List[Symbol], references: List[Reference]):
        """Extract method information"""
        name = ""
        return_type = ""
        modifiers = []
        params = []

        for child in node.children:
            if child.type == 'identifier':
                name = self._get_node_text(child, content_bytes)
            elif child.type == 'modifier':
                modifiers.append(self._get_node_text(child, content_bytes))
            elif child.type in ('predefined_type', 'identifier', 'qualified_name', 'generic_name', 'nullable_type', 'array_type'):
                if not name:
                    return_type = self._get_node_text(child, content_bytes)
            elif child.type == 'parameter_list':
                params = self._extract_params(child, content_bytes)

        if name and 'public' in modifiers:
            line = node.start_point[0] + 1
            param_str = ', '.join(params) if params else ''
            signature = f"{return_type} {name}({param_str})"

            symbols.append(Symbol(
                name=name,
                kind='method',
                file=file_path,
                line=line,
                signature=signature,
                namespace=namespace,
                parent_class=class_name,
                modifiers=modifiers
            ))

    def _extract_property(self, node, content_bytes: bytes, file_path: str, namespace: str,
                          class_name: str, symbols: List[Symbol]):
        """Extract property information"""
        name = ""
        prop_type = ""
        modifiers = []

        for child in node.children:
            if child.type == 'identifier':
                name = self._get_node_text(child, content_bytes)
            elif child.type == 'modifier':
                modifiers.append(self._get_node_text(child, content_bytes))
            elif child.type in ('predefined_type', 'identifier', 'qualified_name', 'generic_name', 'nullable_type'):
                if not name:
                    prop_type = self._get_node_text(child, content_bytes)

        if name and 'public' in modifiers:
            line = node.start_point[0] + 1
            signature = f"{prop_type} {name} {{ get; set; }}"

            symbols.append(Symbol(
                name=name,
                kind='property',
                file=file_path,
                line=line,
                signature=signature,
                namespace=namespace,
                parent_class=class_name,
                modifiers=modifiers
            ))

    def _extract_params(self, node, content_bytes: bytes) -> List[str]:
        """Extract parameter list"""
        params = []
        for child in node.children:
            if child.type == 'parameter':
                param_text = self._get_node_text(child, content_bytes).strip()
                parts = param_text.split()
                if len(parts) >= 2:
                    params.append(f"{parts[-2]} {parts[-1]}")
                elif parts:
                    params.append(parts[-1])
        return params

    def _build_type_signature(self, name: str, base: str, interfaces: List[str],
                               modifiers: List[str], kind: str) -> str:
        """Build a type signature string"""
        mod_str = ' '.join(modifiers) + ' ' if modifiers else ''

        inheritance = []
        if base:
            inheritance.append(base)
        inheritance.extend(interfaces)

        if inheritance:
            return f"{mod_str}{kind} {name} : {', '.join(inheritance)}"
        return f"{mod_str}{kind} {name}"

    # =========== Regex Fallback ===========

    def _parse_with_regex(self, content: str, file_path: str) -> Tuple[List[Symbol], List[Reference]]:
        """Fallback parser using regex"""
        symbols = []
        references = []

        namespace_match = re.search(r'namespace\s+([\w.]+)\s*[{;]', content)
        namespace = namespace_match.group(1) if namespace_match else ""

        class_pattern = r'(public|internal)\s*(abstract\s+|sealed\s+|static\s+|partial\s+)*class\s+(\w+)(?:\s*<[^>]+>)?(?:\s*:\s*([^{]+))?\s*\{'

        for match in re.finditer(class_pattern, content):
            visibility = match.group(1)
            modifiers_str = match.group(2) or ''
            class_name = match.group(3)
            inheritance_str = match.group(4) or ''

            modifiers = [visibility]
            if 'abstract' in modifiers_str:
                modifiers.append('abstract')
            if 'static' in modifiers_str:
                modifiers.append('static')
            if 'partial' in modifiers_str:
                modifiers.append('partial')

            base_class = ""
            interfaces = []
            if inheritance_str:
                parts = self._parse_inheritance_string(inheritance_str)
                for part in parts:
                    if re.match(r'^I[A-Z]', part):
                        interfaces.append(part)
                    elif not base_class:
                        base_class = part

            line = content[:match.start()].count('\n') + 1
            signature = self._build_type_signature(class_name, base_class, interfaces, modifiers, 'class')

            symbols.append(Symbol(
                name=class_name,
                kind='class',
                file=file_path,
                line=line,
                signature=signature,
                namespace=namespace,
                base_class=base_class,
                interfaces=interfaces,
                modifiers=modifiers
            ))

            if base_class:
                references.append(Reference(
                    from_file=file_path,
                    from_symbol=class_name,
                    to_symbol=base_class,
                    ref_type='inherits'
                ))
            for iface in interfaces:
                references.append(Reference(
                    from_file=file_path,
                    from_symbol=class_name,
                    to_symbol=iface,
                    ref_type='implements'
                ))

        return symbols, references

    def _parse_inheritance_string(self, inheritance_str: str) -> List[str]:
        """Parse inheritance string handling generics"""
        parts = []
        current = []
        depth = 0

        for char in inheritance_str:
            if char == '<':
                depth += 1
            elif char == '>':
                depth -= 1
            elif char == ',' and depth == 0:
                part = ''.join(current).strip()
                if part:
                    part = re.sub(r'<[^>]+>', '<T>', part)
                    parts.append(part)
                current = []
                continue
            current.append(char)

        if current:
            part = ''.join(current).strip()
            if part:
                part = re.sub(r'<[^>]+>', '<T>', part)
                parts.append(part)

        return parts


if __name__ == "__main__":
    parser = CSharpParser()

    if len(sys.argv) > 1:
        test_file = Path(sys.argv[1])
        base = test_file.parent
        symbols, refs = parser.parse_file(test_file, base)

        print(f"Symbols ({len(symbols)}):")
        for s in symbols[:10]:
            print(f"  {s.kind}: {s.name} @ {s.file}:{s.line}")

        print(f"\nReferences ({len(refs)}):")
        for r in refs[:10]:
            print(f"  {r.from_symbol} --{r.ref_type}--> {r.to_symbol}")
    else:
        print("Usage: python parser.py <file.cs>")
