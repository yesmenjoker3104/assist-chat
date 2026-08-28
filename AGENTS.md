# Assist Chat エージェント向け開発指示

## プロジェクト概要

ローカルLLM（Ollama）で自然文を解釈し、Google Workspace CLI（gws）経由でGoogle Calendarを操作するWindows向けPyQt6アプリ。

## 起動と環境

- 作業ディレクトリ: `C:\Users\USER\Documents\workspace\assist-chat`
- Python環境: プロジェクト内の`.venv`
- 起動: `.\run.ps1`
- Pythonの依存パッケージは必ず`.venv\Scripts\python.exe`へインストールする。
- システムPythonへ`pip install`しない。
- `.venv`作成時に限り、システムPythonをブートストラップとして使用してよい。

## コア構成

| ファイル | 責務 |
|---|---|
| `main.py` | アプリケーション起動 |
| `backend/llm_engine.py` | Ollama REST APIラッパー |
| `backend/google_calendar.py` | gws呼び出し、Calendar API操作 |
| `config.py` | 設定とプロジェクト内データパス |
| `gui/main_window.py` | メインウィンドウとOllama起動管理 |
| `gui/chat_widget.py` | チャットUI、LLM解釈、操作確認フロー |
| `setup.ps1` | gcloud、gws、Ollama、LLMモデルの初回準備 |
| `run.ps1` | `.venv`、依存関係、セットアップ、アプリ起動 |

## 安全上のルール

- Calendarの予定追加・更新・削除は、実行前に必ずユーザー確認を表示する。
- Gmail操作を追加する場合も、送信・削除・変更は確認を必須にする。
- OAuthのClient Secret、アクセストークン、メール本文などの秘密情報をログやチャットへ出力しない。
- `gws`の権限は必要最小限にし、権限追加時はREADMEとユーザーへ明示する。
- ユーザーの既存変更を勝手に削除・巻き戻ししない。

## OllamaとGoogle CLI

- Ollamaはプロジェクト内の`ollama\ollama.exe`だけを使用し、PATHやシステムインストール版は使用しない。未配置時はsetup.ps1が公式配布ZIPをollamaフォルダへ展開する。
- Ollamaモデル格納先はプロジェクト内の`models`を基本とする。
- Ollama本体・モデルのダウンロードはsetup.ps1が行い、ユーザーへ個別操作を要求しない。
- Google連携には`gcloud`と`gws`が必要。`setup.ps1`で検出・導入する。
- Google OAuthの同意、アカウント選択、テストユーザー設定はブラウザーでのユーザー操作が必要。

## 編集後の検証

最初に最小範囲で次を実行する。

```powershell
python -m py_compile config.py backend\llm_engine.py backend\google_calendar.py main.py
```

PowerShellを変更した場合は、実行前に構文解析する。

```powershell
[scriptblock]::Create((Get-Content .\run.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content .\setup.ps1 -Raw)) | Out-Null
```

READMEと起動手順を変更した場合は、このファイルの関連ルールも更新する。
