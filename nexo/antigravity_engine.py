"""
Atlantis Studio — Antigravity Customization & Plugin Engine
Compatible with all Google Antigravity (AGY) sources, plugins, skills, rules, and MCP servers.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file (e.g. SKILL.md)."""
    meta: dict[str, Any] = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_yaml = parts[1]
            body = parts[2].strip()
            for line in raw_yaml.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip quotes
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    meta[key] = val

    return meta, body


class AntigravityEngine:
    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.home_dir = Path(os.environ.get("HOME", "/home/daniel-sosa"))
        self.state_file = self.workspace_root / ".agents" / "state.json"

        # Well-known Antigravity source paths
        self.builtin_skill_paths = [
            self.home_dir / ".gemini/antigravity/builtin/skills",
            self.home_dir / ".gemini/antigravity-cli/builtin/skills",
        ]
        self.global_config_path = self.home_dir / ".gemini/config"

    def _get_disabled_map(self) -> dict[str, bool]:
        """Read disabled plugins/skills from state file or global config."""
        disabled: dict[str, bool] = {}
        if self.state_file.is_file():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                disabled = data.get("disabled", {})
            except Exception:
                pass
        return disabled

    def _save_disabled_map(self, disabled: dict[str, bool]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"disabled": disabled}, indent=2), encoding="utf-8")

    def toggle_item(self, item_id: str, enabled: bool) -> bool:
        disabled = self._get_disabled_map()
        if enabled:
            disabled.pop(item_id, None)
        else:
            disabled[item_id] = True
        self._save_disabled_map(disabled)
        return True

    def get_custom_sources(self) -> list[str]:
        if self.state_file.is_file():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                return data.get("custom_sources", [])
            except Exception:
                pass
        return []

    def add_custom_source(self, path_str: str) -> bool:
        p = Path(path_str).resolve()
        if not p.exists():
            return False
        sources = self.get_custom_sources()
        if str(p) not in sources:
            sources.append(str(p))
            state_data = {}
            if self.state_file.is_file():
                try:
                    state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            state_data["custom_sources"] = sources
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        return True

    def remove_custom_source(self, path_str: str) -> bool:
        sources = self.get_custom_sources()
        if path_str in sources:
            sources.remove(path_str)
            state_data = {}
            if self.state_file.is_file():
                try:
                    state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            state_data["custom_sources"] = sources
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        return True

    def install_skill(self, skill_id: str, name: str, description: str, instructions: str) -> dict[str, Any]:
        """Create or install an Antigravity skill in workspace skills/ directory."""
        clean_id = re.sub(r'[^a-zA-Z0-9_\-]', '-', skill_id.strip()).lower()
        target_dir = self.workspace_root / "skills" / clean_id
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_dir / "SKILL.md"
        content = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{instructions}\n"
        skill_file.write_text(content, encoding="utf-8")
        return {"id": clean_id, "name": name, "path": str(target_dir), "skill_file": str(skill_file)}

    def install_plugin(self, plugin_id: str, name: str, description: str, version: str = "1.0.0") -> dict[str, Any]:
        """Create or install an Antigravity plugin in workspace .agents/plugins/ directory."""
        clean_id = re.sub(r'[^a-zA-Z0-9_\-]', '-', plugin_id.strip()).lower()
        target_dir = self.workspace_root / ".agents" / "plugins" / clean_id
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = target_dir / "plugin.json"
        manifest = {
            "name": name,
            "description": description,
            "version": version,
            "disabled": False
        }
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (target_dir / "skills").mkdir(exist_ok=True)
        (target_dir / "rules").mkdir(exist_ok=True)
        return {"id": clean_id, "name": name, "path": str(target_dir), "manifest_file": str(manifest_file)}

    def discover_sources(self) -> list[dict[str, Any]]:
        """List all Antigravity discovery root sources."""
        sources = []
        # 1. Workspace
        ws_agents = self.workspace_root / ".agents"
        sources.append({
            "name": "Workspace (.agents)",
            "tipo": "workspace",
            "path": str(ws_agents),
            "exists": ws_agents.is_dir()
        })
        ws_skills = self.workspace_root / "skills"
        if ws_skills.is_dir():
            sources.append({
                "name": "Workspace Skills (skills/)",
                "tipo": "workspace",
                "path": str(ws_skills),
                "exists": True
            })
        ws_plugins = self.workspace_root / "plugins"
        if ws_plugins.is_dir():
            sources.append({
                "name": "Workspace Plugins (plugins/)",
                "tipo": "workspace",
                "path": str(ws_plugins),
                "exists": True
            })

        # 2. Builtin Antigravity
        for p in self.builtin_skill_paths:
            if p.is_dir():
                sources.append({
                    "name": f"Antigravity Builtin ({p.parent.name})",
                    "tipo": "builtin",
                    "path": str(p),
                    "exists": True
                })

        # 3. Global config
        if self.global_config_path.is_dir():
            sources.append({
                "name": "Antigravity Global Config (~/.gemini/config)",
                "tipo": "global",
                "path": str(self.global_config_path),
                "exists": True
            })

        # 4. Custom Registered Sources
        for cs in self.get_custom_sources():
            cp = Path(cs)
            sources.append({
                "name": f"Custom Source ({cp.name})",
                "tipo": "custom",
                "path": cs,
                "exists": cp.exists()
            })

        return sources

    def discover_skills(self) -> list[dict[str, Any]]:
        """Discover all skills across workspace, plugins, and built-in roots."""
        skills: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        disabled_map = self._get_disabled_map()

        search_roots = [
            (self.workspace_root / ".agents" / "skills", "workspace"),
            (self.workspace_root / "skills", "workspace"),
        ]
        # Also check plugins in workspace
        for p_parent in [self.workspace_root / ".agents" / "plugins", self.workspace_root / "plugins"]:
            if p_parent.is_dir():
                for plug_dir in p_parent.iterdir():
                    if plug_dir.is_dir():
                        plug_skills = plug_dir / "skills"
                        if plug_skills.is_dir():
                            search_roots.append((plug_skills, f"plugin:{plug_dir.name}"))

        # Add builtin paths
        for bp in self.builtin_skill_paths:
            if bp.is_dir():
                search_roots.append((bp, "builtin"))

        # Add custom sources
        for cs in self.get_custom_sources():
            cp = Path(cs)
            if cp.is_dir():
                if (cp / "SKILL.md").is_file():
                    search_roots.append((cp.parent, "custom"))
                else:
                    search_roots.append((cp, "custom"))
                    if (cp / "skills").is_dir():
                        search_roots.append((cp / "skills", "custom"))

        for root_path, source_type in search_roots:
            if not root_path.is_dir():
                continue
            for item in sorted(root_path.iterdir()):
                if not item.is_dir():
                    continue
                skill_file = item / "SKILL.md"
                if not skill_file.is_file():
                    continue

                skill_id = item.name
                if skill_id in seen_names:
                    continue
                seen_names.add(skill_id)

                try:
                    content = skill_file.read_text(encoding="utf-8", errors="replace")
                    meta, body = parse_frontmatter(content)
                    display_name = meta.get("name", skill_id)
                    description = meta.get("description", body[:160].replace("\n", " ").strip())

                    # Check for scripts and examples
                    scripts = [s.name for s in (item / "scripts").iterdir()] if (item / "scripts").is_dir() else []
                    examples = [e.name for e in (item / "examples").iterdir()] if (item / "examples").is_dir() else []

                    skills.append({
                        "id": skill_id,
                        "name": display_name,
                        "description": description,
                        "source": source_type,
                        "path": str(item),
                        "skill_file": str(skill_file),
                        "enabled": not disabled_map.get(skill_id, False),
                        "scripts": scripts,
                        "examples": examples,
                        "preview": body[:350]
                    })
                except Exception as e:
                    print(f"[antigravity] Error parsing skill {item}: {e}")

        return skills

    def discover_plugins(self) -> list[dict[str, Any]]:
        """Discover plugins matching the Antigravity plugin specification."""
        plugins: list[dict[str, Any]] = []
        disabled_map = self._get_disabled_map()

        search_dirs = [
            (self.workspace_root / ".agents" / "plugins", "workspace"),
            (self.workspace_root / "plugins", "workspace"),
            (self.home_dir / ".gemini/antigravity/plugins", "global"),
        ]

        for pdir, source_type in search_dirs:
            if not pdir.is_dir():
                continue
            for item in sorted(pdir.iterdir()):
                if not item.is_dir():
                    continue
                manifest_file = item / "plugin.json"
                if not manifest_file.is_file():
                    continue

                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    plug_id = item.name
                    name = manifest.get("name", plug_id)
                    desc = manifest.get("description", "Plugin compatible con Antigravity")
                    version = manifest.get("version", "1.0.0")

                    # Check bundled skills
                    skills_dir = item / "skills"
                    bundled_skills = []
                    if skills_dir.is_dir():
                        for s in skills_dir.iterdir():
                            if (s / "SKILL.md").is_file():
                                bundled_skills.append(s.name)

                    # Check rules
                    rules_dir = item / "rules"
                    has_rules = rules_dir.is_dir() and any(rules_dir.iterdir())

                    # Check MCP
                    mcp_file = item / "mcp_config.json"
                    has_mcp = mcp_file.is_file()

                    # Check Hooks
                    hooks_file = item / "hooks.json"
                    has_hooks = hooks_file.is_file()

                    plugins.append({
                        "id": plug_id,
                        "name": name,
                        "description": desc,
                        "version": version,
                        "source": source_type,
                        "path": str(item),
                        "enabled": not disabled_map.get(plug_id, manifest.get("disabled", False)),
                        "bundled_skills": bundled_skills,
                        "has_rules": has_rules,
                        "has_mcp": has_mcp,
                        "has_hooks": has_hooks
                    })
                except Exception as e:
                    print(f"[antigravity] Error parsing plugin {item}: {e}")

        return plugins

    def discover_rules(self) -> list[dict[str, Any]]:
        """Find all AGENTS.md and GEMINI.md hierarchical rules."""
        rules: list[dict[str, Any]] = []

        candidate_paths = [
            (self.workspace_root / "AGENTS.md", "workspace-root"),
            (self.workspace_root / "GEMINI.md", "workspace-root"),
            (self.workspace_root / ".agents" / "rules", "workspace-rules"),
            (self.home_dir / ".gemini/config/AGENTS.md", "global"),
        ]

        for p, scope in candidate_paths:
            if p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    rules.append({
                        "name": p.name,
                        "scope": scope,
                        "path": str(p),
                        "content": text,
                        "preview": text[:200]
                    })
                except Exception:
                    pass
            elif p.is_dir():
                for f in p.glob("*.md"):
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                        rules.append({
                            "name": f.name,
                            "scope": scope,
                            "path": str(f),
                            "content": text,
                            "preview": text[:200]
                        })
                    except Exception:
                        pass

        return rules

    def discover_mcp(self) -> dict[str, Any]:
        """Find and parse MCP server configurations."""
        servers: dict[str, Any] = {}

        candidate_configs = [
            self.workspace_root / ".agents" / "mcp_config.json",
            self.workspace_root / "mcp_config.json",
            self.home_dir / ".gemini/config/mcp_config.json",
        ]

        for p in candidate_configs:
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    srvs = data.get("mcpServers", {})
                    for s_name, s_def in srvs.items():
                        if s_name not in servers:
                            servers[s_name] = {**s_def, "source": str(p)}
                except Exception:
                    pass

        return servers

    def consolidated_context_for_hermes(self) -> str:
        """Build context injection for Hermes agents including active rules & skills."""
        parts = []

        # 1. Rules
        rules = self.discover_rules()
        if rules:
            parts.append("### REGLAS DE DESARROLLO (Antigravity Rules):")
            for r in rules:
                parts.append(f"--- Regla: {r['name']} ({r['scope']}) ---")
                parts.append(r['content'].strip())

        # 2. Available Skills
        skills = [s for s in self.discover_skills() if s.get("enabled")]
        if skills:
            parts.append("\n### SKILLS DISPONIBLES DE ANTIGRAVITY (Capacidades y Procedimientos):")
            for s in skills:
                parts.append(f"- **{s['name']}** (`{s['id']}`): {s['description']}")

        # 3. MCP Servers
        mcp = self.discover_mcp()
        if mcp:
            parts.append("\n### SERVIDORES MCP ACTIVOS:")
            for s_name, s_def in mcp.items():
                parts.append(f"- **{s_name}**: comando `{s_def.get('command')}`")

        return "\n".join(parts)


# Singleton helper
_engine_instance: AntigravityEngine | None = None

def get_antigravity_engine(workspace: Path | None = None) -> AntigravityEngine:
    global _engine_instance
    if _engine_instance is None or (workspace and _engine_instance.workspace_root != workspace):
        from pathlib import Path
        ws = workspace or Path("/home/daniel-sosa/.gemini/antigravity/scratch/atlantis_proyect")
        _engine_instance = AntigravityEngine(ws)
    return _engine_instance
