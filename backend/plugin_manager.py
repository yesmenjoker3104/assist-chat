"""Plugin manager — discovers and executes modules in plugin/."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# CLI の実行上限（秒）。LLM 呼び出しに費やした時間は加算される。
PLUGIN_TIMEOUT_SEC = 60
# 1 回のアクション中に CLI が LLM を呼べる最大回数。
MAX_LLM_REQUESTS = 5


def _pump_lines(stream, out: queue.Queue) -> None:
    """stdout を行単位で読み、キューへ流す。EOF では None を入れる。"""
    try:
        for line in stream:
            out.put(line.rstrip("\r\n"))
    finally:
        out.put(None)


def _parse_llm_request(line: str) -> dict | None:
    """llm_request 制御行なら dict を返す。通常の出力行なら None。"""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("type") == "llm_request":
        return data
    return None


def _kill(process: subprocess.Popen) -> None:
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


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
    # Relative paths or folder names that updates must neither overwrite nor delete
    preserve: list[str] = field(default_factory=list)

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
            preserve=[str(p) for p in data.get("preserve", [])],
        )


class PluginManager:
    """Scans plugin/ for plugin.json manifests and routes actions."""

    def __init__(self, base_dir: Path, settings: dict | None = None):
        self.runtime_dir = base_dir / "plugin"
        self.plugins: list[Plugin] = []
        self._action_map: dict[str, tuple[Plugin, PluginAction]] = {}
        self.settings: dict = settings or {}
        self._load()

    def reload(self) -> None:
        self.plugins = []
        self._action_map = {}
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

    def _run(
        self,
        plugin: Plugin,
        cmd: list[str],
        llm_complete: Callable[[str], str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        """CLI を実行し、stdout の最終 JSON を返す。

        stdout に `{"type": "llm_request", "id", "prompt"}` の行があれば LLM に問い合わせ、
        `{"type": "llm_response", "id", "text"|"error"}` を stdin へ 1 行で返す。
        それ以外の出力は従来どおり結果 JSON として扱う。
        """
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._build_env(plugin),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_pump_lines, args=(process.stdout, lines), daemon=True).start()

        body: list[str] = []
        deadline = time.monotonic() + PLUGIN_TIMEOUT_SEC
        llm_calls = 0
        try:
            while True:
                if is_cancelled and is_cancelled():
                    _kill(process)
                    return {"ok": False, "error": "キャンセルされました。"}
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill(process)
                    return {"ok": False, "error": "タイムアウト"}
                try:
                    line = lines.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if line is None:
                    break
                request = _parse_llm_request(line)
                if request is None:
                    body.append(line)
                    continue

                request_id = request.get("id")
                if llm_complete is None:
                    reply = {"type": "llm_response", "id": request_id, "error": "LLMは利用できません。"}
                elif llm_calls >= MAX_LLM_REQUESTS:
                    reply = {"type": "llm_response", "id": request_id, "error": "LLM呼び出しの上限に達しました。"}
                else:
                    llm_calls += 1
                    started = time.monotonic()
                    try:
                        reply = {
                            "type": "llm_response",
                            "id": request_id,
                            "text": llm_complete(str(request.get("prompt", ""))),
                        }
                    except Exception as exc:
                        reply = {"type": "llm_response", "id": request_id, "error": str(exc)}
                    # LLM の待ち時間は CLI のタイムアウトに含めない
                    deadline += time.monotonic() - started
                try:
                    process.stdin.write(json.dumps(reply, ensure_ascii=True) + "\n")
                    process.stdin.flush()
                except (OSError, ValueError):
                    # CLI が stdin を閉じている場合は、結果の受け取りだけを続ける
                    pass

            process.wait(timeout=max(deadline - time.monotonic(), 1))
        except subprocess.TimeoutExpired:
            _kill(process)
            return {"ok": False, "error": "タイムアウト"}
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

        output = "\n".join(body).strip()
        if not output:
            return {"ok": False, "error": f"{plugin.name} から出力がありませんでした。"}
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"JSON解析エラー: {exc}"}

    def execute(
        self,
        plugin: Plugin,
        action: PluginAction,
        params: dict,
        llm_complete: Callable[[str], str] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict:
        """Build CLI args from manifest + params, run the executable, return parsed JSON."""
        args = self._build_args(action, params)
        cmd = [str(plugin.executable)] + action.command_subcommands + args
        return self._run(plugin, cmd, llm_complete, is_cancelled)

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
