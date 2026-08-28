import json
import subprocess
from typing import Iterator

import requests


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
