import json
import subprocess
import os
import subprocess
from typing import Iterator

import requests

from config import OLLAMA_EXE, OLLAMA_MODELS_DIR


class OllamaEngine:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def health_check(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return response.ok
        except requests.RequestException:
            return False

    def has_model(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return any(item.get("name") == self.model for item in response.json().get("models", []))
        except (requests.RequestException, ValueError):
            return False

    def get_available_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            return sorted(item["name"] for item in response.json().get("models", []) if item.get("name"))
        except (requests.RequestException, ValueError, KeyError):
            return []

    def delete_model(self, model_name: str) -> tuple[bool, str]:
        try:
            response = requests.delete(
                f"{self.base_url}/api/delete",
                json={"model": model_name},
                timeout=30,
            )
            response.raise_for_status()
            return True, ""
        except requests.RequestException as exc:
            return False, str(exc)

    def pull_model(self, model_name: str, progress_callback=None) -> tuple[bool, str]:
        if not OLLAMA_EXE.exists():
            error = "プロジェクト内のollama\\ollama.exeが見つかりません。"
            if progress_callback:
                progress_callback(error)
            return False, error
        try:
            process = subprocess.Popen(
                [str(OLLAMA_EXE), "pull", model_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "OLLAMA_MODELS": str(OLLAMA_MODELS_DIR)},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output_lines = []
            for line in process.stdout or []:
                stripped = line.strip()
                if stripped:
                    output_lines.append(stripped)
                    if progress_callback:
                        progress_callback(stripped)
            success = process.wait() == 0
            if success:
                return True, ""
            error = "\n".join(output_lines[-3:]) or "ollama pullがエラーコードを返しました。"
            return False, error
        except OSError as exc:
            error = f"モデルのダウンロードに失敗しました: {exc}"
            if progress_callback:
                progress_callback(error)
            return False, error

    def generate(self, prompt: str) -> Iterator[str]:
        payload = {"model": self.model, "prompt": prompt, "stream": True}
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                stream=True,
                timeout=(10, 600),
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    data = json.loads(line)
                    if data.get("response"):
                        yield data["response"]
                    if data.get("done"):
                        break
        except requests.RequestException as exc:
            yield f"[Ollama接続エラー: {exc}]"
