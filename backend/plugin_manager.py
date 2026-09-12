"""Plugin manager — discovers and executes modules in plugin/."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginAction:
    action: str
    prompt_hint: str
    prompt_example: dict
    prompt_notes: str
    command_subcommands: list[str]
    command_args: list[dict]
    # "plugin" = generic executor  /  "builtin:<method>" = delegate to Python
    handler: str
    display: str          # "text" | "table" | "articles_table" | "events_table" | "progress"
    display_field: str | None
    display_template: str | None
    display_columns: list[str] | None
    display_link_column: str | None
    display_url_column: str | None


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    executable: Path
    actions: list[PluginAction] = field(default_factory=list)
    # {ENV_VAR_NAME: settings_key} — resolved from PluginManager settings at execute time
    env_vars: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, plugin_dir: Path) -> "Plugin":
        data = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        executable = plugin_dir / data["executable"]
        actions = [
            PluginAction(
                action=a["action"],
                prompt_hint=a.get("prompt_hint", ""),
                prompt_example=a.get("prompt_example", {}),
                prompt_notes=a.get("prompt_notes", ""),
                command_subcommands=a.get("command", {}).get("subcommands", []),
                command_args=a.get("command", {}).get("args", []),
                handler=a.get("handler", "plugin"),
                display=a.get("display", "text"),
                display_field=a.get("display_field"),
                display_template=a.get("display_template"),
                display_columns=a.get("display_columns"),
                display_link_column=a.get("display_link_column"),
                display_url_column=a.get("display_url_column"),
            )
            for a in data.get("actions", [])
        ]
        return cls(
            name=data.get("name", plugin_dir.name),
            version=data.get("version", ""),
            description=data.get("description", ""),
            executable=executable,
            actions=actions,
            env_vars=data.get("env_vars", {}),
        )


class PluginManager:
    """Scans plugin/ for plugin.json manifests and routes actions."""

    def __init__(self, base_dir: Path, settings: dict | None = None):
        self.runtime_dir = base_dir / "plugin"
        self.plugins: list[Plugin] = []
        self._action_map: dict[str, tuple[Plugin, PluginAction]] = {}
        self.settings: dict = settings or {}
        self._load()

    def _load(self) -> None:
        if not self.runtime_dir.exists():
            return
        for entry in sorted(self.runtime_dir.iterdir()):
            if not entry.is_dir() or not (entry / "plugin.json").exists():
                continue
            try:
                plugin = Plugin.load(entry)
                self.plugins.append(plugin)
                for action in plugin.actions:
                    self._action_map[action.action] = (plugin, action)
            except Exception as exc:
                print(f"[PluginManager] {entry.name}: {exc}")

    def build_prompt_lines(self) -> list[str]:
        """Return SYSTEM_PROMPT lines generated from all loaded manifests."""
        lines: list[str] = []
        for plugin in self.plugins:
            for action in plugin.actions:
                example = json.dumps(action.prompt_example, ensure_ascii=False)
                line = f"    {action.prompt_hint}: {example}"
                if action.prompt_notes:
                    line += f"\n      ※ {action.prompt_notes}"
                lines.append(line)
        return lines

    def find(self, action_name: str) -> tuple[Plugin, PluginAction] | None:
        return self._action_map.get(action_name)

    def _build_env(self, plugin: Plugin) -> dict | None:
        """Build environment dict with plugin-declared env_vars resolved from settings."""
        if not plugin.env_vars:
            return None
        env = os.environ.copy()
        for env_key, settings_key in plugin.env_vars.items():
            value = self.settings.get(settings_key)
            if value:
                env[env_key] = str(value)
        return env

    def _run(self, plugin: Plugin, cmd: list[str]) -> dict:
        """Execute a CLI command for the given plugin and return parsed JSON."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=self._build_env(plugin),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = result.stdout.strip()
            if not output:
                return {"ok": False, "error": f"{plugin.name} から出力がありませんでした。"}
            return json.loads(output)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "タイムアウト"}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"JSON解析エラー: {exc}"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def execute(self, plugin: Plugin, action: PluginAction, params: dict) -> dict:
        """Build CLI args from manifest + params, run the executable, return parsed JSON."""
        args = self._build_args(action, params)
        cmd = [str(plugin.executable)] + action.command_subcommands + args
        return self._run(plugin, cmd)

    def execute_raw(self, plugin: Plugin, subcommands: list[str], args: list[str]) -> dict:
        """Execute plugin CLI with explicit subcommands and args, bypassing manifest arg mapping."""
        cmd = [str(plugin.executable)] + subcommands + args
        return self._run(plugin, cmd)

    def _build_args(self, action: PluginAction, params: dict) -> list[str]:
        args: list[str] = []
        for arg in action.command_args:
            t = arg.get("type", "positional")
            if t == "positional":
                fixed = arg.get("fixed_value")
                value = fixed if fixed is not None else params.get(arg.get("param", ""), arg.get("default", ""))
                if value is None:
                    value = arg.get("default", "")
                args.append(str(value))
            elif t == "flag":
                fixed = arg.get("fixed_value")
                if fixed is not None:
                    args.extend([arg["flag"], str(fixed)])
                    continue
                param = arg.get("param", "")
                value = params.get(param)
                if arg.get("omit_if_null") and value is None:
                    continue
                if value is None:
                    value = arg.get("default")
                if value is not None:
                    # Apply min/max constraints (e.g. for --limit)
                    if "min" in arg or "max" in arg:
                        try:
                            v = int(value)
                            v = max(arg.get("min", v), v)
                            v = min(arg.get("max", v), v)
                            value = v
                        except (ValueError, TypeError):
                            value = arg.get("default", value)
                    args.extend([arg["flag"], str(value)])
            elif t == "bool_flag":
                if params.get(arg.get("param", ""), False):
                    args.append(arg["flag"])
        return args
