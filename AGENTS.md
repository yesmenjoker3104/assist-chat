# Assist Chat エージェント向け開発指示

## プロジェクト概要

ローカルLLM（Ollama）で自然文を解釈し、Google Workspace CLI（gws）経由でGoogle Calendarを操作するWindows向けPyQt6アプリ。`plugin/` にモジュールを追加するだけで機能を拡張できるプラグインプラットフォームでもある。

## 起動と環境

- 作業ディレクトリ: `D:\dev\assist-chat`
- Python環境: プロジェクト内の`.venv`
- 起動: `.\run.ps1`（開発時）。exe版は `.\build.ps1` で `AssistChat.exe` をプロジェクト直下に生成して起動する。
- Pythonの依存パッケージは必ず`.venv\Scripts\python.exe`へインストールする。
- システムPythonへ`pip install`しない。
- `.venv`作成時に限り、システムPythonをブートストラップとして使用してよい。
- exe版（`sys.frozen`）では`config.BASE_DIR`がexeの配置フォルダになる。新しいパスは`config.BASE_DIR`/`config.RESOURCE_DIR`から組み立て、`__file__`を基準にしない。
- 凍結時の`sys.executable`はアプリ本体のため、Pythonとして実行しない（`backend/video_downloader.py`の`_python_interpreter()`を使う）。

## コア構成

| ファイル | 責務 |
|---|---|
| `main.py` | アプリケーション起動 |
| `backend/llm_engine.py` | Ollama REST APIラッパー |
| `backend/plugin_manager.py` | プラグイン発見・ルーティング・CLI実行 |
| `config.py` | 設定とプロジェクト内データパス |
| `gui/main_window.py` | メインウィンドウとOllama起動管理 |
| `gui/chat_widget.py` | チャットUI、LLM解釈、操作確認フロー |
| `setup.ps1` | gcloud、gws、Ollama、LLMモデルの初回準備 |
| `run.ps1` | `.venv`、依存関係、セットアップ、アプリ起動 |
| `build.ps1` | PyInstallerで`AssistChat.exe`（ウィンドウ版・onefile）をプロジェクト直下へビルド |
| `version.py` | `__version__`。リリースタグ（`vX.Y.Z`）と一致させる |
| `backend/updater.py` | GitHub Releasesの最新版確認、exeダウンロード、差し替え用PowerShell起動 |
| `.github/workflows/windows-release.yml` | `v*`タグでexeをビルドし、GitHub Releasesへ`AssistChat.exe`を公開 |

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

## プラグイン契約（plugin.json）

`plugin/<ModuleName>/plugin.json` を置くと起動時に自動認識される。コードの変更不要。

### マニフェスト構造

```jsonc
{
  "name": "ModuleName",
  "version": "1.0.0",
  "description": "モジュールの説明",
  "executable": "ModuleCli.exe",         // plugin/<ModuleName>/ 以下の相対パス
  "actions": [
    {
      "action": "action_name",           // LLMが返すJSONの "action" 値
      "prompt_hint": "自然言語の説明",     // SYSTEM_PROMPTへ挿入される行頭テキスト
      "prompt_example": { "action": "..." },  // LLMへ示すJSON例（適切な型で記載）
      "prompt_notes": "補足（省略可）",    // ※ で始まる注釈として挿入
      "command": {
        "subcommands": ["sub", "cmd"],   // executable の後ろに付くサブコマンド
        "args": [                        // positional / flag / bool_flag
          { "type": "positional", "param": "query", "default": "" },
          { "type": "flag",       "flag": "--limit", "param": "limit", "default": 10, "min": 1, "max": 100 },
          { "type": "flag",       "flag": "--source", "param": "source", "omit_if_null": true },
          { "type": "bool_flag",  "flag": "--unread", "param": "unread" }
        ]
      },
      "handler": "plugin",              // "plugin"=汎用CLI実行 / "builtin:<method>"=Python委譲
      "display": "text",                // "text" | "table" | "articles_table" | "events_table" | "progress"
      "display_template": "結果: {field}",  // display=text 時のテンプレート（省略可）
      "display_field": "items",         // display=table/articles_table 時に使うJSONフィールド名
      "display_columns": ["col1","col2"],// display=table 時に表示するカラム（省略=全列）
      "display_link_column": "title",   // リンクテキストとして使うカラム名（省略可）
      "display_url_column": "url"       // リンク先URLカラム名（省略可）
    }
  ]
}
```

### CLIの出力規約（`handler: "plugin"` のみ適用）

- **stdout に必ず1行のJSONを出力する**（他の出力はstderrへ）
- フォーマット: `{"ok": true, ...}` または `{"ok": false, "error": "説明"}`
- `ensure_ascii=True` で出力する（日本語はUnicodeエスケープ）
- exit code: 0（成功）/ 非0（失敗）両方でJSONを出力すること

### handler の種類

| 値 | 動作 |
|---|---|
| `"plugin"` | PluginManagerがCLIを直接実行し、stdout JSONを `display` 設定で表示 |
| `"builtin:<method>"` | 既存のPythonメソッド（`_call_builtin`マップ）へ委譲。CLIを呼ばない |

`"progress"` 表示（ダウンロード進捗など）や複数回のCLI呼び出しが必要な複雑な処理は `handler: "builtin:..."` として実装する。

### display 設定と表示方式

| display | 表示方式 | 必須フィールド |
|---|---|---|
| `"text"` | `display_template` を結果JSONの値で置換してバブル表示 | `display_template`（省略時はJSON文字列） |
| `"table"` | `display_field` のリストを `display_columns` でHTMLテーブル表示 | `display_field`, `display_columns` |
| `"articles_table"` | 記事専用レンダラー（ソース別見出し＋タイトルリンク） | `display_field` |
| `"events_table"` | カレンダー予定専用レンダラー（日付・時刻・タイトル表示） | `display_field` |
| `"progress"` | スレッド進捗バブル（builtin専用） | — |

### モジュール提供者の責任

- `prompt_hint` と `prompt_example` でLLMが正しいアクションを選べるよう記述する
- JSON型を正確に: 数値は `10`、真偽値は `true/false`、省略可能フィールドは `null`
- `prompt_notes` で誤解しやすい点（ソース名の指定方法、limitの範囲など）を補足する
- CLIのexitコードと出力形式を契約通りに実装する

## 編集後の検証

最初に最小範囲で次を実行する。

```powershell
python -m py_compile config.py backend\llm_engine.py backend\plugin_manager.py gui\chat_widget.py gui\main_window.py main.py
```

PowerShellを変更した場合は、実行前に構文解析する。

```powershell
[scriptblock]::Create((Get-Content .\run.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content .\setup.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content .\build.ps1 -Raw)) | Out-Null
```

READMEと起動手順を変更した場合は、このファイルの関連ルールも更新する。
