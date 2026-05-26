# Claude Code skills

Ready-to-install [Claude Code](https://claude.com/claude-code) skills that drive `coderepomap` from natural-language prompts (English or Chinese).

## Available

| Skill | Purpose |
|---|---|
| [`repomap`](repomap/SKILL.md) | Detect project shape (C# / Lua / mixed), init or reuse config, run `python -m coderepomap generate`, summarize the result. Triggers on "generate code map", "update repomap", "扫一下代码结构", "生成 repomap" etc. |

## Install

Skills live under `~/.claude/skills/<name>/` (user-scope, available in every project) or `<project-root>/.claude/skills/<name>/` (project-scope, only here).

### User scope (recommended — works in any cwd)

PowerShell:

```powershell
$src = "<this-repo>\skills\repomap"
$dst = "$env:USERPROFILE\.claude\skills\repomap"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "$src\SKILL.md" "$dst\SKILL.md" -Force
```

Bash / git-bash:

```bash
mkdir -p ~/.claude/skills/repomap
cp <this-repo>/skills/repomap/SKILL.md ~/.claude/skills/repomap/SKILL.md
```

After copy, the skill is auto-discovered on the next Claude Code session start.

### Project scope (only this repo, useful for contributors)

```bash
mkdir -p .claude/skills/repomap
cp skills/repomap/SKILL.md .claude/skills/repomap/SKILL.md
```

## Keep in sync

When this repo's skill is updated, re-copy to refresh your local copy. There's no auto-sync — skills are static markdown.

To check whether your installed copy is up to date:

```bash
diff ~/.claude/skills/repomap/SKILL.md skills/repomap/SKILL.md
```

Empty diff = in sync.

## Skill contract

Each skill is a single `SKILL.md` with YAML frontmatter (`name`, `description`) followed by Markdown body. Claude Code reads the frontmatter at session start, surfaces the skill in its picker, and feeds the body to the model when the skill is invoked.

For the skill authoring spec, see the [`skill-creator`](https://github.com/anthropics/skills) reference upstream or your local `~/.claude/skills/skill-creator/SKILL.md`.
