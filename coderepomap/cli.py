#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI for csharp-repomap

Commands:
    repomap init [--preset unity|generic]   Initialize configuration
    repomap generate [--verbose] [--notify] Generate repo map
    repomap status                          Show current status
    repomap hooks [--install|--uninstall]   Manage Git hooks
"""

import argparse
import json
import sys
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from . import __version__
from .generator import RepoMapGenerator
from .notifier import send_notification, create_windows_notify_script


def get_templates_dir() -> Path:
    """Get path to templates directory"""
    return Path(__file__).parent / 'templates'


def get_resources_dir() -> Path:
    """Get path to resources directory"""
    return Path(__file__).parent / 'resources'


def cmd_init(args):
    """Initialize repomap configuration in current directory"""
    project_root = Path.cwd()
    repomap_dir = project_root / '.repomap'

    if repomap_dir.exists() and not args.force:
        print(f"Error: .repomap directory already exists. Use --force to overwrite.")
        return 1

    # Create directory structure
    repomap_dir.mkdir(parents=True, exist_ok=True)
    (repomap_dir / 'output').mkdir(exist_ok=True)

    # Get preset configuration
    templates_dir = get_templates_dir()
    preset = args.preset or 'generic'

    config_template = templates_dir / f'config.{preset}.yaml'

    if config_template.exists():
        shutil.copy(config_template, repomap_dir / 'config.yaml')
        print(f"Created: .repomap/config.yaml (preset: {preset})")
    else:
        # Create default config
        config = RepoMapGenerator._get_default_config()
        config['project_name'] = project_root.name

        if HAS_YAML:
            with open(repomap_dir / 'config.yaml', 'w', encoding='utf-8') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        else:
            with open(repomap_dir / 'config.json', 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

        print(f"Created: .repomap/config.{'yaml' if HAS_YAML else 'json'}")

    # Create .gitignore for output
    gitignore_path = repomap_dir / '.gitignore'
    gitignore_path.write_text("# Ignore generated output\noutput/\n", encoding='utf-8')
    print("Created: .repomap/.gitignore")

    print(f"\nInitialized csharp-repomap in {project_root}")
    print("\nNext steps:")
    print("  1. Edit .repomap/config.yaml to configure source paths")
    print("  2. Run 'repomap generate' to generate the repo map")
    print("  3. Run 'repomap hooks --install' to enable Git hooks")

    return 0


def cmd_generate(args):
    """Generate repo map"""
    project_root = Path.cwd()
    config_path = project_root / '.repomap' / 'config.yaml'

    # Also check for .json config
    if not config_path.exists():
        config_path = project_root / '.repomap' / 'config.json'

    if not config_path.exists():
        print("Error: No configuration found. Run 'repomap init' first.")
        return 1

    # Load config
    if config_path.suffix == '.yaml' and HAS_YAML:
        config = RepoMapGenerator.load_config(config_path)
    else:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

    # Override output directory to .repomap/output
    config['output']['directory'] = '.repomap/output'

    # Create generator and run
    try:
        generator = RepoMapGenerator(config=config, project_root=project_root)
        results = generator.run(verbose=args.verbose)

        if results['success']:
            # Write update log
            _write_update_log(project_root, results)

            # Send notification if requested
            if args.notify:
                send_notification(
                    title="RepoMap Updated",
                    message=f"{results['file_count']} files | {results['duration']:.1f}s"
                )

            return 0
        else:
            return 1

    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Error during generation: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


def _write_update_log(project_root: Path, results: dict):
    """Write update log entry"""
    log_path = project_root / '.repomap' / 'update.log'
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Get git info
    import subprocess
    try:
        branch = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, cwd=str(project_root)
        ).stdout.strip()
        commit = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, cwd=str(project_root)
        ).stdout.strip()
    except Exception:
        branch = "unknown"
        commit = "unknown"

    entry = f"[{timestamp}] {branch:20} | {commit} | {results['file_count']} files | {results['duration']:.1f}s\n"

    # Append to log (keep last 50 entries)
    existing_lines = []
    if log_path.exists():
        with open(log_path, 'r', encoding='utf-8') as f:
            existing_lines = f.readlines()[-49:]

    with open(log_path, 'w', encoding='utf-8') as f:
        f.writelines(existing_lines)
        f.write(entry)


def cmd_status(args):
    """Show current repomap status"""
    project_root = Path.cwd()
    repomap_dir = project_root / '.repomap'

    if not repomap_dir.exists():
        print("Status: Not initialized")
        print("\nRun 'repomap init' to initialize.")
        return 0

    print(f"RepoMap Status for: {project_root.name}")
    print("=" * 50)

    # Check config
    config_yaml = repomap_dir / 'config.yaml'
    config_json = repomap_dir / 'config.json'

    if config_yaml.exists():
        print(f"Config: {config_yaml.relative_to(project_root)}")
    elif config_json.exists():
        print(f"Config: {config_json.relative_to(project_root)}")
    else:
        print("Config: Not found")

    # Check output files
    output_dir = repomap_dir / 'output'
    meta_path = output_dir / 'repomap-meta.json'

    if meta_path.exists():
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)

        print(f"\nLast Generated: {meta.get('generated_at', 'unknown')}")
        print(f"Git Commit: {meta.get('git_commit', 'unknown')[:8]}")
        print(f"Git Branch: {meta.get('git_branch', 'unknown')}")

        stats = meta.get('stats', {})
        print(f"\nStatistics:")
        print(f"  Files: {stats.get('file_count', 0)}")
        print(f"  Classes: {stats.get('class_count', 0)}")
        print(f"  Methods: {stats.get('method_count', 0)}")
        print(f"  References: {stats.get('reference_count', 0)}")
        print(f"  Modules: {stats.get('module_count', 0)}")

        print(f"\nTop Modules:")
        for m in meta.get('top_modules', [])[:5]:
            print(f"  - {m['name']} ({m['classes']} classes)")

    else:
        print("\nOutput: Not generated yet")
        print("Run 'repomap generate' to create the repo map.")

    # Check hooks
    hooks_installed = _check_hooks_installed(project_root)
    print(f"\nGit Hooks: {'Installed' if hooks_installed else 'Not installed'}")

    # Show recent updates
    log_path = repomap_dir / 'update.log'
    if log_path.exists():
        print("\nRecent Updates:")
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-5:]
            for line in lines:
                print(f"  {line.rstrip()}")

    return 0


def _check_hooks_installed(project_root: Path) -> bool:
    """Check if Git hooks are installed"""
    hooks_dir = project_root / '.git' / 'hooks'
    post_merge = hooks_dir / 'post-merge'

    if post_merge.exists():
        content = post_merge.read_text(encoding='utf-8', errors='ignore')
        return 'repomap' in content.lower()

    return False


def cmd_hooks(args):
    """Manage Git hooks"""
    project_root = Path.cwd()
    git_dir = project_root / '.git'

    if not git_dir.exists():
        print("Error: Not a Git repository.")
        return 1

    hooks_dir = git_dir / 'hooks'
    hooks_dir.mkdir(exist_ok=True)

    if args.uninstall:
        return _uninstall_hooks(hooks_dir)
    else:
        return _install_hooks(project_root, hooks_dir, args.with_notify)


def _install_hooks(project_root: Path, hooks_dir: Path, with_notify: bool = True) -> int:
    """Install Git hooks"""
    templates_dir = get_templates_dir() / 'hooks'

    # Hook names to install
    hook_names = ['post-merge', 'post-checkout']

    for hook_name in hook_names:
        hook_path = hooks_dir / hook_name
        template_path = templates_dir / hook_name

        if template_path.exists():
            content = template_path.read_text(encoding='utf-8')
        else:
            # Generate default hook content
            content = _generate_hook_content(hook_name, with_notify)

        # Check for existing hook
        if hook_path.exists():
            existing = hook_path.read_text(encoding='utf-8', errors='ignore')
            if 'repomap' not in existing.lower():
                # Backup existing hook
                backup_path = hook_path.with_suffix('.backup')
                shutil.copy(hook_path, backup_path)
                print(f"Backed up existing {hook_name} to {hook_name}.backup")

                # Append our hook
                content = existing.rstrip() + '\n\n# RepoMap auto-update\n' + content

        hook_path.write_text(content, encoding='utf-8')

        # Make executable on Unix
        if sys.platform != 'win32':
            hook_path.chmod(0o755)

        print(f"Installed: {hook_name}")

    # Create notify script for Windows
    if sys.platform == 'win32' and with_notify:
        resources_dir = project_root / '.repomap'
        create_windows_notify_script(resources_dir)
        print("Created: .repomap/notify.ps1")

    print("\nGit hooks installed successfully!")
    print("RepoMap will auto-update on: git pull, git merge, git checkout")

    return 0


def _uninstall_hooks(hooks_dir: Path) -> int:
    """Uninstall Git hooks"""
    hook_names = ['post-merge', 'post-checkout']

    for hook_name in hook_names:
        hook_path = hooks_dir / hook_name

        if not hook_path.exists():
            continue

        content = hook_path.read_text(encoding='utf-8', errors='ignore')

        if 'repomap' in content.lower():
            # Check if it's only our hook
            lines = content.split('\n')
            non_repomap_lines = [
                l for l in lines
                if 'repomap' not in l.lower() and l.strip() and not l.startswith('#')
            ]

            if not non_repomap_lines:
                # Only our hook, remove entirely
                hook_path.unlink()
                print(f"Removed: {hook_name}")

                # Restore backup if exists
                backup_path = hook_path.with_suffix('.backup')
                if backup_path.exists():
                    shutil.move(backup_path, hook_path)
                    print(f"Restored: {hook_name} from backup")
            else:
                # Other hooks exist, just remove our section
                # Find and remove RepoMap section
                new_content = _remove_repomap_section(content)
                hook_path.write_text(new_content, encoding='utf-8')
                print(f"Removed RepoMap section from: {hook_name}")

    print("\nGit hooks uninstalled.")
    return 0


def _remove_repomap_section(content: str) -> str:
    """Remove RepoMap section from hook content"""
    lines = content.split('\n')
    result = []
    skip = False

    for line in lines:
        if '# RepoMap' in line or '# repomap' in line:
            skip = True
            continue
        if skip and (line.startswith('#') or not line.strip()):
            continue
        if skip and 'repomap' in line.lower():
            continue
        skip = False
        result.append(line)

    return '\n'.join(result)


def _generate_hook_content(hook_name: str, with_notify: bool) -> str:
    """Generate hook content"""
    notify_flag = ' --notify' if with_notify else ''

    if sys.platform == 'win32':
        # PowerShell/Batch compatible
        return f'''#!/bin/sh
# RepoMap auto-update hook

# Check if repomap is available
if command -v repomap >/dev/null 2>&1; then
    # Check if source files changed
    CHANGED=$(git diff --name-only HEAD@{{1}} HEAD 2>/dev/null | grep -E "\\.cs$" | head -1)
    if [ -n "$CHANGED" ]; then
        echo "C# files changed, updating RepoMap..."
        repomap generate{notify_flag}
    fi
fi
'''
    else:
        return f'''#!/bin/sh
# RepoMap auto-update hook

# Check if repomap is available
if command -v repomap >/dev/null 2>&1; then
    # Check if source files changed
    CHANGED=$(git diff --name-only HEAD@{{1}} HEAD 2>/dev/null | grep -E "\\.cs$" | head -1)
    if [ -n "$CHANGED" ]; then
        echo "C# files changed, updating RepoMap..."
        repomap generate{notify_flag}
    fi
fi
'''


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='repomap',
        description='Generate layered code maps for C# projects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  repomap init --preset unity    Initialize with Unity preset
  repomap generate --verbose     Generate repo map with verbose output
  repomap status                 Show current status
  repomap hooks --install        Install Git hooks for auto-update
'''
    )

    parser.add_argument(
        '--version', '-V',
        action='version',
        version=f'csharp-repomap {__version__}'
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # init command
    init_parser = subparsers.add_parser('init', help='Initialize configuration')
    init_parser.add_argument(
        '--preset', '-p',
        choices=['unity', 'generic'],
        default='generic',
        help='Configuration preset (default: generic)'
    )
    init_parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Overwrite existing configuration'
    )

    # generate command
    gen_parser = subparsers.add_parser('generate', help='Generate repo map')
    gen_parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Verbose output'
    )
    gen_parser.add_argument(
        '--notify', '-n',
        action='store_true',
        help='Send notification when complete'
    )

    # status command
    subparsers.add_parser('status', help='Show current status')

    # hooks command
    hooks_parser = subparsers.add_parser('hooks', help='Manage Git hooks')
    hooks_group = hooks_parser.add_mutually_exclusive_group()
    hooks_group.add_argument(
        '--install', '-i',
        action='store_true',
        default=True,
        help='Install Git hooks (default)'
    )
    hooks_group.add_argument(
        '--uninstall', '-u',
        action='store_true',
        help='Uninstall Git hooks'
    )
    hooks_parser.add_argument(
        '--with-notify',
        action='store_true',
        default=True,
        help='Enable notifications in hooks (default: True)'
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    # Dispatch to command handler
    if args.command == 'init':
        return cmd_init(args)
    elif args.command == 'generate':
        return cmd_generate(args)
    elif args.command == 'status':
        return cmd_status(args)
    elif args.command == 'hooks':
        return cmd_hooks(args)
    else:
        parser.print_help()
        return 0


if __name__ == '__main__':
    sys.exit(main())
