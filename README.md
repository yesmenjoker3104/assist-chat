# Assist Chat

ローカルLLM（Ollama）で自然文を解釈し、Google Workspace CLI（gws）経由でGoogle Calendarを操作するPyQt6アプリです。`plugin/` にモジュールを置くだけでLLMができることが増えるプラグインプラットフォームでもあります。

## 初回セットアップと起動

```powershell
cd D:\dev\assist-chat
.\run.ps1
```

`run.ps1`がプロジェクト内の`.venv`を作成し、そのPythonへ依存パッケージをインストールしてから、同じ`.venv`のPythonでアプリを起動します。システムPythonへ依存パッケージはインストールしません。通常起動ではセットアップ処理を実行せず、すぐに画面を表示します。

初回またはOllama・Google CLIの準備が必要な場合だけ、次を実行します。

```powershell
.\run.ps1 -Setup
```

`-Setup` は `gcloud` と `gws` を確認し、Ollama公式配布ZIPを`ollama\`へ展開して、Ollama本体とモデルを準備します。

Google Calendar/GmailのOAuth認証は、Googleアカウントの許可が必要なため、初回だけ次を実行してください。

```powershell
gws auth setup --login
```

## プラグインシステム

`plugin/<ModuleName>/` にモジュールフォルダを置き、`plugin.json` マニフェストを追加するだけで、起動時に自動認識されてLLMが新しい操作を理解するようになります。コードの変更は不要です。

### 仕組み

1. 起動時に `plugin/` をスキャンし、`plugin.json` を持つフォルダをプラグインとして読み込む
2. 各プラグインの `prompt_hint` と `prompt_example` が SYSTEM_PROMPT に自動挿入される
3. LLMがユーザーの依頼を JSON コマンドに変換し、`action` 名でルーティング
4. `handler: "plugin"` → マニフェストの `command` テンプレートに従って EXE を直接実行
5. `handler: "builtin:<method>"` → assist-chat 側の Python メソッドへ委譲（複雑な処理用）
6. 実行結果を `display` 設定（text / table / articles_table / events_table / progress）に従って表示

### 標準モジュール

| フォルダ | 概要 |
|---|---|
| `plugin/GCalCtrl/` | Google Calendarの予定確認・追加・削除（[gcal-ctrl](https://github.com/yesmenjoker3104/gcal-ctrl) ビルド成果物） |
| `plugin/GetNewTechInfo/` | 技術記事の取得・検索・ソース管理（RSS/Atom） |
| `plugin/YoutubeAudioDownloader/` | YouTube等からの動画・音声ダウンロード |

### 新しいモジュールの追加方法

```
plugin/
  YourModule/
    YourModuleCli.exe   ← CLIの実行ファイル
    plugin.json         ← マニフェスト
```

**plugin.json 最小例:**

```json
{
  "name": "YourModule",
  "version": "1.0.0",
  "description": "モジュールの説明",
  "executable": "YourModuleCli.exe",
  "actions": [
    {
      "action": "your_action",
      "prompt_hint": "ユーザーがどう言ったらこのアクションを使うか",
      "prompt_example": { "action": "your_action", "param1": "値" },
      "command": {
        "subcommands": [],
        "args": [
          { "type": "flag", "flag": "--param1", "param": "param1", "omit_if_null": true }
        ]
      },
      "handler": "plugin",
      "display": "text",
      "display_template": "結果: {result_field}"
    }
  ]
}
```

**CLIの出力規約:**
- stdout に1行のJSON: `{"ok": true, ...}` または `{"ok": false, "error": "説明"}`
- 日本語は `ensure_ascii=True`（Unicodeエスケープ）で出力する
- ログや進捗はstderrへ

詳細は `AGENTS.md` のプラグイン契約セクションを参照してください。

## 設定

メニューバーの「設定」から、Ollama接続先、使用モデル、gwsコマンド、動画保存先、技術記事の自動取得を変更できます。使用モデルは、一覧から選択するかモデル名を直接入力してダウンロードし、そのまま切り替えられます。

設定は自動的に`data/settings.json`へ保存されます。手動で設定する場合は次の形式を使用します。

```json
{
  "ollama_url": "http://127.0.0.1:11434",
  "model": "qwen2.5:7b",
  "gws_command": "gws",
  "youtube_downloader_dir": null,
  "youtube_download_output_dir": null,
  "youtube_download_parallel": 3,
  "tech_news_dir": "D:\\dev\\assist-chat\\plugin\\GetNewTechInfo",
  "tech_news_cli_exe": "D:\\dev\\assist-chat\\plugin\\GetNewTechInfo\\GetNewTechInfoCli.exe",
  "tech_news_auto_fetch_enabled": true,
  "tech_news_auto_fetch_minutes": 30
}
```

`youtube_download_output_dir` を `null` にすると、保存先未指定時はシステムの Downloads フォルダを使用します。

`tech_news_auto_fetch_enabled` が `true` のとき、`tech_news_auto_fetch_minutes`（分）ごとに技術記事をバックグラウンドで取得し、新着がある場合だけチャットに通知します。

gwsがPATHにない環境では、`gws_command`に実行ファイルのフルパスを指定できます。OllamaはPATHから探さず、必ず`ollama\ollama.exe`を使います。
