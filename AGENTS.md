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
| `backend/ollama_installer.py` | 未配置時のOllama公式ZIPダウンロード・展開、サーバー起動 |
| `backend/plugin_manager.py` | プラグイン発見・ルーティング・CLI実行・`reload()` |
| `backend/plugin_installer.py` | プラグインZIPのURL取得・検証・展開・上書き導入、おすすめカタログ取得 |
| `gui/plugin_install_dialog.py` | おすすめプラグイン一覧ダイアログ |
| `plugin_catalog.json` | おすすめプラグイン一覧（`https://raw.githubusercontent.com/yesmenjoker3104/assist-chat/master/plugin_catalog.json` で取得）。各項目に `name`・`description`・`url` を書く。`url` はプラグイン専用リポジトリの `https://github.com/<owner>/<repo>/releases/latest/download/<ファイル名>.zip` とし、版は書かない（最新版はアプリがリリースタグから取得する）。ZIPの中の `plugin.json` の版とリリースのタグ（`vX.Y.Z`）は一致させる |
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

- Ollamaはプロジェクト内の`ollama\ollama.exe`だけを使用し、PATHやシステムインストール版は使用しない。未配置時はアプリ起動時に`backend/ollama_installer.py`が公式配布ZIPをollamaフォルダへ展開する（exeは同梱しない）。
- Ollamaモデル格納先はプロジェクト内の`models`を基本とする。
- Ollama本体・モデルの初回取得は`gui/main_window.py`の`prepare_ollama`が行い、ユーザーへ個別操作を要求しない。`setup.ps1`は開発環境・gws準備用として残す。
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
  "preserve": ["config.json"],           // 省略可。更新時に上書き・削除しないファイル/フォルダ（相対パス）
  "login": {                             // 省略可。認証が必要なときチャットに出す「認証を開く」ボタン
    "subcommands": ["auth"],             // ボタン押下時に executable へ渡すサブコマンド
    "label": "Google認証を開く"            // ボタンのラベル（省略時は「認証を開く」）
  },
  "watch": {                             // 省略可。定期実行して通知を表示する（例: リマインダー）
    "subcommands": ["remind"],           // 定期実行するサブコマンド（結果の notices を表示）
    "interval_minutes": 5                // 実行間隔（分、省略時5）
  },
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
          // flag の値が配列・オブジェクトの場合は JSON 文字列（ensure_ascii=False）として渡される
        ]
      },
      "handler": "plugin",              // "plugin"=汎用CLI実行 / "builtin:<method>"=Python委譲
      "display": "text",                // "text" | "table" | "articles_table" | "progress"
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

### LLMコールバック（任意・`handler: "plugin"` のみ）

CLIがLLMへ問い合わせて結果を加工したい場合は、stdoutへ次の制御行を出力する。

- CLI → ホスト（stdout）: `{"type": "llm_request", "id": "<任意の文字列>", "prompt": "..."}`
- ホスト → CLI（stdin、1行のJSON）: `{"type": "llm_response", "id": "<同じid>", "text": "..."}` または `{"type": "llm_response", "id": "<同じid>", "error": "..."}`

- 制御行以外のstdout行は従来どおり結果として扱い、最後にJSONとして解析する。制御行を出さないCLIは従来と同じ動作。
- 1アクションあたりのLLM呼び出しは最大5回。超過時は `error` が返る。
- CLIの実行タイムアウト（60秒）にはLLMの待ち時間を含めない。
- LLM接続が無い場合やLLMがエラーを返した場合も `llm_response` が返るので、CLIはエラーとして処理すること。
- プラグイン実行はバックグラウンドスレッドで行われ、LLM待ちの間もUIは操作可能。キャンセルでCLIは強制終了される。

### 確認ダイアログ（confirm・任意・`handler: "plugin"` のみ）

破壊的な操作（削除・変更など）の前にユーザーへ確認したい場合は、CLIがstdoutへ次の制御行を出力する。

- CLI → ホスト（stdout）: `{"type": "confirm", "id": "<任意の文字列>", "title": "ダイアログ見出し", "message": "本文"}`
- ホスト → CLI（stdin、1行のJSON）: `{"type": "confirm_response", "id": "<同じid>", "confirmed": true}`（「はい」）または `false`（「いいえ」・キャンセル時）

- ホストはQMessageBoxで「はい／いいえ」を表示する。ユーザーの回答待ちはCLIの実行タイムアウトに含めない。
- CLIは `confirmed` が false のとき、実行せずに `{"ok": true, "message": "キャンセルしました。"}` などを返す（`display: "text"` + `display_template: "{message}"` で表示できる）。
- 確認の回答を受け取れないホスト経路（`execute_raw`）では `confirmed` は常に false として扱われる。

### 認証（login）と通知（watch）

- `login`: 認証が必要な結果（`{"ok": false, "auth_required": true}`）を受けると、`login.label` のボタンを表示する。押下で `login.subcommands` を実行する。`login` が無いプラグインは案内文だけを表示する。
- `watch`: 起動時と `interval_minutes` ごとに `watch.subcommands` を実行する（バックグラウンド）。結果の `notices` を表示する。
  - `notices` は `[{"key": "一意キー", "text": "表示する文章"}]`。同じ `key` は一度だけ表示する（プラグイン名とキーの組で重複排除）。
  - `auth_required` が返ったら `login` ボタンを表示する（同じプラグインの認証案内は繰り返し出さない）。

### handler の種類

| 値 | 動作 |
|---|---|
| `"plugin"` | PluginManagerがCLIを直接実行し、stdout JSONを `display` 設定で表示 |
| `"builtin:<method>"` | 既存のPythonメソッド（`_call_builtin`マップ）へ委譲。CLIを呼ばない |

`"progress"` 表示（ダウンロード進捗など）や、Python側での複雑な処理が必要な場合は `handler: "builtin:..."` として実装する。LLMを複数回使う処理は、CLIからの「LLMコールバック」で実装できる（`handler: "plugin"` のまま）。

### display 設定と表示方式

| display | 表示方式 | 必須フィールド |
|---|---|---|
| `"text"` | `display_template` を結果JSONの値で置換してバブル表示 | `display_template`（省略時はJSON文字列） |
| `"table"` | `display_field` のリストを `display_columns` でHTMLテーブル表示（各行の辞書キーは列名と一致させる） | `display_field`, `display_columns` |
| `"articles_table"` | 記事専用レンダラー（ソース別見出し＋タイトルリンク） | `display_field` |
| `"progress"` | スレッド進捗バブル（builtin専用） | — |

### モジュール提供者の責任

- `prompt_hint` と `prompt_example` でLLMが正しいアクションを選べるよう記述する
- JSON型を正確に: 数値は `10`、真偽値は `true/false`、省略可能フィールドは `null`
- `prompt_notes` で誤解しやすい点（ソース名の指定方法、limitの範囲など）を補足する
- CLIのexitコードと出力形式を契約通りに実装する

## 編集後の検証

最初に最小範囲で次を実行する。

```powershell
python -m py_compile config.py backend\llm_engine.py backend\plugin_manager.py backend\plugin_installer.py gui\chat_widget.py gui\main_window.py gui\plugin_install_dialog.py main.py
```

PowerShellを変更した場合は、実行前に構文解析する。

```powershell
[scriptblock]::Create((Get-Content .\run.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content .\setup.ps1 -Raw)) | Out-Null
[scriptblock]::Create((Get-Content .\build.ps1 -Raw)) | Out-Null
```

READMEと起動手順を変更した場合は、このファイルの関連ルールも更新する。
