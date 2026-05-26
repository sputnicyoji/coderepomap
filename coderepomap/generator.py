#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RepoMap Generator for C# Projects

Generates layered code maps optimized for AI assistants:
- L1: Module skeleton (~1k tokens)
- L2: Class signatures (~2k tokens)
- L3: Reference relations (~3k tokens)
"""

import sys
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Handle encoding for Windows
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass
if sys.stderr:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

from .parser import CSharpParser, Symbol, Reference
from .ranker import PageRankRanker


class RepoMapGenerator:
    """Main generator for code repository maps"""

    def __init__(self, config: Optional[dict] = None, project_root: Optional[Path] = None):
        """
        Initialize with configuration.

        Args:
            config: Configuration dictionary (or load from file)
            project_root: Project root directory (defaults to cwd)
        """
        self.config = config or self._get_default_config()
        self.project_root = project_root or Path.cwd()

        self.parser = CSharpParser()
        self.ranker = PageRankRanker(
            alpha=self.config.get('pagerank', {}).get('alpha', 0.85),
            max_iter=self.config.get('pagerank', {}).get('max_iter', 100)
        )

        # Data storage
        self.symbols: List[Symbol] = []
        self.references: List[Reference] = []
        self.modules: Dict[str, dict] = {}

        # Paths
        source_root = self.config.get('source', {}).get('root_path', '.')
        self.source_path = self.project_root / source_root

        output_dir = self.config.get('output', {}).get('directory', '.repomap/output')
        self.output_dir = self.project_root / output_dir

    @staticmethod
    def _get_default_config() -> dict:
        """Get default configuration"""
        return {
            'project_name': 'C# Project',
            'source': {
                'root_path': '.',
                'file_extensions': ['.cs'],
                'exclude_patterns': ['**/bin/**', '**/obj/**', '**/Editor/**', '**/Test/**']
            },
            'tokens': {
                'l1_skeleton': 1000,
                'l2_signatures': 2000,
                'l3_relations': 3000,
                'encoding': 'cl100k_base'
            },
            'pagerank': {
                'alpha': 0.85,
                'max_iter': 100
            },
            'output': {
                'directory': '.repomap/output',
                'files': {
                    'skeleton': 'repomap-L1-skeleton.md',
                    'signatures': 'repomap-L2-signatures.md',
                    'relations': 'repomap-L3-relations.md',
                    'meta': 'repomap-meta.json'
                }
            },
            'importance_boost': {
                'patterns': [
                    {'prefix': 'S', 'boost': 2.0, 'description': 'Service classes'},
                    {'suffix': 'Manager', 'boost': 1.5, 'description': 'Manager classes'},
                    {'suffix': 'Controller', 'boost': 1.5, 'description': 'Controller classes'},
                    {'suffix': 'Service', 'boost': 1.5, 'description': 'Service classes'}
                ],
                'priority_modules': []
            },
            'categories': {
                # Default categories - can be overridden in config
                'Core': {'patterns': ['Core', 'Common', 'Util', 'Base']},
                'Game': {'patterns': ['Game', 'Player', 'Level', 'Scene']},
                'UI': {'patterns': ['UI', 'View', 'Panel', 'Window', 'Dialog']},
                'Data': {'patterns': ['Data', 'Model', 'Entity', 'Config']},
                'Other': {'patterns': []}
            }
        }

    @staticmethod
    def load_config(config_path: Path) -> dict:
        """Load configuration from YAML file"""
        default_config = RepoMapGenerator._get_default_config()

        if HAS_YAML and config_path.exists():
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f)
                    if loaded:
                        RepoMapGenerator._deep_merge(default_config, loaded)
            except Exception as e:
                print(f"Warning: Failed to load config: {e}")

        return default_config

    @staticmethod
    def _deep_merge(base: dict, update: dict):
        """Deep merge update into base"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                RepoMapGenerator._deep_merge(base[key], value)
            else:
                base[key] = value

    def scan_directory(self) -> List[Path]:
        """Scan source directory for C# files"""
        if not self.source_path.exists():
            raise FileNotFoundError(f"Source path not found: {self.source_path}")

        cs_files = []
        exclude_patterns = self.config['source'].get('exclude_patterns', [])

        for ext in self.config['source']['file_extensions']:
            for f in self.source_path.rglob(f'*{ext}'):
                # Check exclusions
                rel_path = str(f.relative_to(self.source_path))
                excluded = False
                for pattern in exclude_patterns:
                    if self._match_pattern(rel_path, pattern):
                        excluded = True
                        break
                if not excluded:
                    cs_files.append(f)

        return cs_files

    def _match_pattern(self, path: str, pattern: str) -> bool:
        """Simple glob pattern matching"""
        import fnmatch
        path = path.replace('\\', '/')
        pattern = pattern.replace('\\', '/')

        if pattern.startswith('**/'):
            return fnmatch.fnmatch(path, pattern[3:]) or '/' + pattern[3:] in '/' + path
        return fnmatch.fnmatch(path, pattern)

    def parse_all_files(self, files: List[Path], verbose: bool = False):
        """Parse all C# files and collect symbols/references"""
        if verbose:
            print(f"Parsing {len(files)} files...")

        boost_patterns = self.config.get('importance_boost', {}).get('patterns', [])

        for i, f in enumerate(files):
            if verbose and (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(files)} files...")

            syms, refs = self.parser.parse_file(f, self.source_path)
            self.symbols.extend(syms)
            self.references.extend(refs)

            # Add to ranker with boost
            for sym in syms:
                if sym.kind == 'class':
                    boost = self._calculate_boost(sym.name, boost_patterns)
                    self.ranker.add_symbol(sym.name, sym.file, sym.kind, boost)

        # Add references to ranker
        for ref in self.references:
            self.ranker.add_reference(ref.from_symbol, ref.to_symbol, ref.ref_type)

        if verbose:
            print(f"  Total: {len(self.symbols)} symbols, {len(self.references)} references")

    def _calculate_boost(self, name: str, patterns: List[dict]) -> float:
        """Calculate importance boost for a symbol based on patterns"""
        boost = 1.0

        for pattern in patterns:
            if 'prefix' in pattern:
                prefix = pattern['prefix']
                if name.startswith(prefix) and len(name) > len(prefix):
                    # Check if next char is uppercase (e.g., SPlayerService vs 'setup')
                    if name[len(prefix)].isupper():
                        boost = max(boost, pattern.get('boost', 1.0))

            if 'suffix' in pattern:
                if name.endswith(pattern['suffix']):
                    boost = max(boost, pattern.get('boost', 1.0))

            if 'contains' in pattern:
                if pattern['contains'] in name:
                    boost = max(boost, pattern.get('boost', 1.0))

        return boost

    def build_module_stats(self):
        """Build module statistics from symbols"""
        for sym in self.symbols:
            if sym.kind != 'class':
                continue

            # Extract module from file path
            parts = sym.file.replace('\\', '/').split('/')
            module = parts[0] if parts else 'Unknown'

            if module not in self.modules:
                self.modules[module] = {
                    'class_count': 0,
                    'classes': [],
                    'files': set()
                }

            self.modules[module]['class_count'] += 1
            self.modules[module]['classes'].append(sym.name)
            self.modules[module]['files'].add(sym.file)

        # Convert sets to counts
        for m in self.modules.values():
            m['file_count'] = len(m['files'])
            del m['files']

    def count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        if HAS_TIKTOKEN:
            try:
                encoding = tiktoken.get_encoding(
                    self.config['tokens'].get('encoding', 'cl100k_base')
                )
                return len(encoding.encode(text))
            except Exception:
                pass

        # Fallback: rough estimate (4 chars per token)
        return len(text) // 4

    def _categorize_module(self, module: str) -> str:
        """Categorize a module based on config patterns"""
        categories = self.config.get('categories', {})

        for cat_name, cat_config in categories.items():
            if cat_name == 'Other':
                continue
            patterns = cat_config.get('patterns', [])
            for pattern in patterns:
                if pattern.lower() in module.lower():
                    return cat_name

        return 'Other'

    def generate_l1_skeleton(self) -> str:
        """Generate L1 module skeleton"""
        max_tokens = self.config['tokens']['l1_skeleton']
        project_name = self.config.get('project_name', 'C# Project')

        # Get module ranks
        module_ranks = self.ranker.get_module_ranks()

        # Sort modules by rank
        sorted_modules = sorted(
            self.modules.items(),
            key=lambda x: module_ranks.get(x[0], 0),
            reverse=True
        )

        # Get git info
        git_commit = self._get_git_hash()

        # Build output
        lines = [
            f"# {project_name} Repo Map (L1)",
            f"> Generated: {datetime.now().strftime('%Y-%m-%d')} | Commit: {git_commit[:8] if git_commit else 'unknown'}",
            "",
            f"## Module Overview ({len(self.modules)} modules)",
            ""
        ]

        # Group modules by category
        categories: Dict[str, List] = defaultdict(list)
        priority_modules = self.config.get('importance_boost', {}).get('priority_modules', [])

        for module, info in sorted_modules:
            rank = module_ranks.get(module, 0)
            cat = self._categorize_module(module)
            categories[cat].append((module, info, rank))

        # Output by category
        for cat_name in self.config.get('categories', {}).keys():
            modules = categories.get(cat_name, [])
            if not modules:
                continue

            lines.append(f"### {cat_name}")
            for module, info, rank in sorted(modules, key=lambda x: x[2], reverse=True)[:10]:
                active = " [Active]" if module in priority_modules else ""
                lines.append(f"- {module}/ ({info['class_count']} classes){active}")
            lines.append("")

        # Core entry classes table
        lines.append("### Core Entry Classes")
        lines.append("| Module | Entry Class | Key Methods |")
        lines.append("|--------|-------------|-------------|")

        # Find important classes (services, managers, controllers)
        ranked_symbols = self.ranker.get_ranked_symbols(limit=20)
        for name, rank, info in ranked_symbols:
            # Find methods for this class
            methods = []
            for sym in self.symbols:
                if sym.parent_class == name and sym.kind == 'method':
                    methods.append(sym.name)
            method_str = ', '.join(methods[:3]) if methods else '-'

            module = info.get('file', '').split('/')[0] if info.get('file') else '-'
            lines.append(f"| {module} | {name} | {method_str} |")

        output = '\n'.join(lines)

        # Trim if over token limit
        while self.count_tokens(output) > max_tokens and len(lines) > 10:
            lines.pop(-2)  # Remove second to last line
            output = '\n'.join(lines)

        return output

    def generate_l2_signatures(self) -> str:
        """Generate L2 class signatures"""
        max_tokens = self.config['tokens']['l2_signatures']
        project_name = self.config.get('project_name', 'C# Project')

        lines = [
            f"# {project_name} Repo Map (L2)",
            "",
        ]

        # Get ranked symbols
        ranked = self.ranker.get_ranked_symbols()

        # Group by module
        module_classes: Dict[str, List] = defaultdict(list)
        for name, rank, info in ranked:
            file = info.get('file', '')
            module = file.split('/')[0] if file else 'Unknown'
            module_classes[module].append((name, rank, info))

        # Sort modules by total rank
        module_ranks = {m: sum(r for _, r, _ in classes) for m, classes in module_classes.items()}
        sorted_modules = sorted(module_ranks.keys(), key=lambda x: module_ranks[x], reverse=True)

        for module in sorted_modules[:15]:  # Top 15 modules
            classes = module_classes[module]
            total_rank = module_ranks[module]

            lines.append(f"## {module} ({len(classes)} classes, rank: {total_rank:.2f})")
            lines.append("")

            # Top classes in this module
            for name, rank, info in sorted(classes, key=lambda x: x[1], reverse=True)[:5]:
                # Find the symbol
                sym = next((s for s in self.symbols if s.name == name and s.kind == 'class'), None)
                if sym:
                    lines.append(f"### {sym.signature}")

                    # Find methods
                    methods = [s for s in self.symbols if s.parent_class == name and s.kind == 'method']
                    for m in methods[:5]:
                        lines.append(f"- {m.signature}")

                    lines.append("")

        output = '\n'.join(lines)

        # Trim if over limit
        while self.count_tokens(output) > max_tokens and lines:
            # Remove last class section
            while lines and not lines[-1].startswith('##'):
                lines.pop()
            if lines:
                lines.pop()
            output = '\n'.join(lines)

        return output

    def generate_l3_relations(self) -> str:
        """Generate L3 reference relations"""
        max_tokens = self.config['tokens']['l3_relations']
        project_name = self.config.get('project_name', 'C# Project')

        lines = [
            f"# {project_name} Repo Map (L3)",
            "",
            "## Reference Graph",
            ""
        ]

        # Build incoming/outgoing reference maps
        incoming: Dict[str, List] = defaultdict(list)
        outgoing: Dict[str, List] = defaultdict(list)

        for ref in self.references:
            incoming[ref.to_symbol].append((ref.from_symbol, ref.ref_type))
            outgoing[ref.from_symbol].append((ref.to_symbol, ref.ref_type))

        # Get ranked symbols
        ranked = self.ranker.get_ranked_symbols(limit=30)

        for name, rank, info in ranked:
            in_refs = incoming.get(name, [])
            out_refs = outgoing.get(name, [])

            if not in_refs and not out_refs:
                continue

            lines.append(f"{name} (refs: {len(in_refs)}, rank: {rank:.2f})")

            # Outgoing
            for target, ref_type in out_refs[:5]:
                lines.append(f"  -> {target} ({ref_type})")

            # Incoming
            for source, ref_type in in_refs[:3]:
                lines.append(f"  <- {source} ({ref_type})")

            lines.append("")

        output = '\n'.join(lines)

        # Trim if needed
        while self.count_tokens(output) > max_tokens and len(lines) > 5:
            lines.pop()
            output = '\n'.join(lines)

        return output

    def generate_meta(self) -> dict:
        """Generate metadata JSON"""
        return {
            'project_name': self.config.get('project_name', 'C# Project'),
            'git_commit': self._get_git_hash(),
            'git_branch': self._get_git_branch(),
            'generated_at': datetime.now().isoformat(),
            'source_path': self.config['source']['root_path'],
            'stats': {
                'file_count': len(set(s.file for s in self.symbols)),
                'class_count': len([s for s in self.symbols if s.kind == 'class']),
                'method_count': len([s for s in self.symbols if s.kind == 'method']),
                'reference_count': len(self.references),
                'module_count': len(self.modules),
            },
            'top_modules': [
                {'name': m, 'classes': info['class_count']}
                for m, info in sorted(
                    self.modules.items(),
                    key=lambda x: x[1]['class_count'],
                    reverse=True
                )[:10]
            ],
            'ranker_stats': self.ranker.get_stats()
        }

    def _get_git_hash(self) -> str:
        """Get current git commit hash"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True, text=True,
                cwd=str(self.project_root)
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def _get_git_branch(self) -> str:
        """Get current git branch"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True,
                cwd=str(self.project_root)
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    def save_all(self, verbose: bool = False):
        """Generate and save all output files"""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        files = self.config['output']['files']

        # L1 Skeleton
        l1_path = self.output_dir / files['skeleton']
        l1_content = self.generate_l1_skeleton()
        l1_path.write_text(l1_content, encoding='utf-8')
        if verbose:
            print(f"Generated: {l1_path} ({self.count_tokens(l1_content)} tokens)")

        # L2 Signatures
        l2_path = self.output_dir / files['signatures']
        l2_content = self.generate_l2_signatures()
        l2_path.write_text(l2_content, encoding='utf-8')
        if verbose:
            print(f"Generated: {l2_path} ({self.count_tokens(l2_content)} tokens)")

        # L3 Relations
        l3_path = self.output_dir / files['relations']
        l3_content = self.generate_l3_relations()
        l3_path.write_text(l3_content, encoding='utf-8')
        if verbose:
            print(f"Generated: {l3_path} ({self.count_tokens(l3_content)} tokens)")

        # Meta JSON
        meta_path = self.output_dir / files['meta']
        meta = self.generate_meta()
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
        if verbose:
            print(f"Generated: {meta_path}")

        return {
            'l1': {'path': l1_path, 'tokens': self.count_tokens(l1_content)},
            'l2': {'path': l2_path, 'tokens': self.count_tokens(l2_content)},
            'l3': {'path': l3_path, 'tokens': self.count_tokens(l3_content)},
            'meta': {'path': meta_path}
        }

    def run(self, verbose: bool = True) -> dict:
        """
        Main execution.

        Returns:
            Dictionary with generation results
        """
        start_time = datetime.now()
        project_name = self.config.get('project_name', 'C# Project')

        if verbose:
            print("=" * 60)
            print(f"RepoMap Generator - {project_name}")
            print("=" * 60)

        # Scan files
        files = self.scan_directory()
        if verbose:
            print(f"Found {len(files)} C# files in {self.source_path}")

        # Parse
        self.parse_all_files(files, verbose)

        # Build stats
        self.build_module_stats()

        # Generate outputs
        results = self.save_all(verbose)

        # Calculate duration
        duration = (datetime.now() - start_time).total_seconds()

        if verbose:
            print("=" * 60)
            print(f"Done! ({duration:.1f}s)")

        return {
            'success': True,
            'duration': duration,
            'file_count': len(files),
            'results': results
        }
