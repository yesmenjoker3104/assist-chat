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

## exe で起動する

プロジェクトフォルダで次を実行すると、プロジェクト直下に `AssistChat.exe` を作成できます。PyInstaller は `.venv` に導入されます。

```powershell
.\build.ps1
```

以後は `AssistChat.exe` をダブルクリックして起動できます（コンソール画面は表示されません）。

- exe は自身と同じフォルダの `data/`・`plugin/`・`ollama/`・`models/` を使います。exe はプロジェクトフォルダ内に置いてください（別の場所へ移すと、設定・履歴・プラグインが見つかりません）。
- 初回起動時に `ollama\ollama.exe` が無ければ、Ollama公式配布ZIPを自動でダウンロードして `ollama\` へ展開し、設定中のモデルも取得します。Ollamaとモデルは数GB規模なので、通信環境の良い場所で起動してください。Ollamaは同梱していません。
- 取得に失敗した場合はアプリを再起動して再試行するか、「設定」からモデルを取得してください。
- ソースを変更したら `.\build.ps1` を再実行してください。

## バージョン管理と自動更新

- バージョンは `version.py` の `__version__` で管理します。画面のメニュー「ヘルプ > バージョン情報」で確認できます。
- 「ヘルプ > 更新を確認」で GitHub Releases の最新版を確認し、新しければ `AssistChat.exe` をダウンロードして差し替え、再起動します。更新は exe 版のみ対応です（ソースから起動した場合は案内のみ表示されます）。
- 差し替えは exe 1 ファイルだけです。`data/`・`plugin/`・`ollama/`・`models/` は変更されません。

リリース手順:

1. `version.py` の `__version__` を新しいバージョン（例: `0.2.0`）に上げてコミットします。
2. タグ `v0.2.0` を作成して push します（タグ名と `version.py` が一致している必要があります）。
3. GitHub Actions の `windows-release.yml` が `build.ps1` で exe をビルドし、GitHub Releases に `AssistChat.exe` を公開します。

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

標準のプラグインは `plugin/` に入っていません。「プラグイン > おすすめプラグインから導入...」で導入すると `plugin/<名前>/` に配置されます。

| フォルダ | 概要 |
|---|---|
| `plugin/GmailCtrl/` | Gmail の受信メール一覧の取得と下書き作成（おすすめカタログから導入） |
| `plugin/GetNewTechInfo/` | 技術記事の取得・検索・ソース管理（RSS/Atom、おすすめカタログから導入） |
| `plugin/WebSearch/` | DuckDuckGo による Web 検索と要約（おすすめカタログから導入） |
| `plugin/YoutubeAudioDownloader/` | YouTube等からの動画・音声ダウンロード（カタログ未掲載のため手動で配置） |

### プラグインの導入（アプリ内）

「プラグイン」メニューから導入できます。アプリを再起動する必要はありません。

- **おすすめプラグインから導入...**: リポジトリの `plugin_catalog.json` に載っているプラグイン一覧から、導入・更新したいものにチェックを入れて、まとめて導入できます。
- **URLからプラグインを導入...**: プラグインのZIPファイルの `https://` URL を指定して導入します。

導入時は提供元と警告を表示して確認します。複数件をまとめて選んだ場合も、確認は1回だけです。おすすめ一覧には「未導入／更新あり（vX → vY）／最新」が表示され、「更新ありを全て選択」ボタンで更新があるものを一度にチェックできます。導入は1件ずつ順番に行い、途中で失敗したものがあっても残りは続けて導入します。最後に成功・失敗の一覧を表示します。同じ名前のプラグインは上書きします（データファイルは残ります）。

おすすめ一覧の更新判定は、カタログの URL（`https://github.com/<owner>/<repo>/releases/latest/download/<ファイル名>.zip`）から GitHub の最新リリースのタグを取得し、導入済みの `plugin.json` の版と比べて行います。カタログには版を書きません。タグ（`vX.Y.Z`）と ZIP 内 `plugin.json` の版は一致させてください。最新版を取得できない場合（オフラインやAPI制限など）は「導入済み」と表示します。

更新時は次のように扱います。
- 新しいZIPに含まれるファイルは上書きします。
- 前回の導入で入ったファイルのうち、新しいZIPに含まれないものは削除します（導入時に `.install_manifest.json` に記録したファイルだけが対象です）。
- `plugin.json` の `preserve` に書いたファイル・フォルダは、上書きも削除もしません。
- プラグイン実行時に作られたデータ（DBなど）は導入の記録に含まれないため残ります。
- 更新するファイルがロックされている場合（実行中のexeなど）は、何も変更せずに「プラグインが実行中でないか確認して」と案内して中止します。
- 途中で失敗した場合は、更新前の状態に戻します。
- 導入記録（`.install_manifest.json`）がない旧版からの初回更新では、古いファイルは削除されません。
- プラグイン導入処理のテストは `python -m unittest tests.test_plugin_installer` で実行できます。

ZIPの中身は、`plugin.json` のあるフォルダ1つを含む形（または `plugin.json` をルートに置く形）にしてください。

### プラグインの配布方法（開発者向け）

おすすめプラグインは、プラグインごとの公開リポジトリの最新リリース（Latest）から配布します。

1. **リポジトリを公開する**: `releases/latest` は匿名で取得できるよう、公開リポジトリにしておきます。
2. **ZIPを作る**: `plugin.json` と実行ファイルを含むZIPを作ります（上記の中身のルールに従う）。
3. **リリースを作る**: タグは `vX.Y.Z`（`plugin.json` の `version` と一致させる）。Pre-release にせず、**Latest** として公開します。アセット名はカタログの URL に合わせて固定します（例: `WebSearch.zip`）。
4. **カタログに登録する**: `plugin_catalog.json` に `name`・`description`・`url` を書きます。`url` は `https://github.com/<owner>/<repo>/releases/latest/download/<アセット名>` の形式で、版は書きません。
5. **カタログを push する**: アプリは `master` の `plugin_catalog.json` を読みます。push 後に反映されます。

更新するときは、新しいバージョンでリリース（Latest）を作るだけです。カタログは変更不要です。アプリは最新リリースのタグと導入済みの版を比べて「更新あり」を表示します。

- Pre-release（`dev-latest` など）は Latest に含まれないため、更新判定の対象になりません。
- 最新リリースが無い、または API の制限に当たった場合は「導入済み」と表示され、導入自体はできます（ZIPが無い場合は導入に失敗します）。
- 現在の配布元: GmailCtrl（`yesmenjoker3104/gmail-ctrl`）、GetNewTechInfo（`yesmenjoker3104/get_new_tech_info`）、WebSearch（`yesmenjoker3104/web-search`）。
- 旧 `assist-chat` の `plugin-*` リリースは残しています。カタログからは参照しません。

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
