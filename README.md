# Assist Chat

ローカルLLM（Ollama）で自然文を解釈し、Google Workspace CLI（gws）経由でGoogle Calendarを操作するPyQt6アプリです。

構成は`knowledge_chat`に合わせ、`backend/`にLLM・Google連携、`gui/`に画面部品、`main.py`に起動処理を配置します。

## 初回セットアップと起動

```powershell
cd C:\Users\USER\Documents\workspace\assist-chat
.\run.ps1
```

`run.ps1`がプロジェクト内の`.venv`を作成し、そのPythonへ依存パッケージをインストールしてから、同じ`.venv`のPythonでアプリを起動します。システムPythonへ依存パッケージはインストールしません。通常起動ではセットアップ処理を実行せず、すぐに画面を表示します。

初回またはOllama・Google CLIの準備が必要な場合だけ、次を実行します。

```powershell
.\run.ps1 -Setup
```

`-Setup` は `gcloud` と `gws` を確認し、Ollama公式配布ZIPを`ollama\`へ展開して、Ollama本体とモデルを準備します。

`setup.ps1`がOllama公式配布物をプロジェクト内の`ollama\`へダウンロード・展開し、LLMモデルもプロジェクト内の`models`へ取得します。ユーザーが手動で別のダウンローダーを操作する必要はありません。

Google Calendar/GmailのOAuth認証は、Googleアカウントの許可が必要なため、初回だけ次を実行してください。

```powershell
gws auth setup --login
```

認証済みの`gcloud`と`gws`を使い、Calendarの読み取りは自動実行します。予定の追加・削除は確認ダイアログを表示します。

YouTube動画のダウンロード依頼（例: 「この動画をダウンロードして https://...」）にも対応しています。`youtube-audio-downloader` リポジトリのCLIを呼び出して実行します。

技術記事の検索・取得依頼にも対応しています。`get_new_tech_info` のGitHub ReleaseからWindows版CLIを取得し、`release_runtime\GetNewTechInfo\GetNewTechInfoCli.exe` に配置してください。

## 設定

必要なら`data/settings.json`を作成します。

```json
{
  "ollama_url": "http://127.0.0.1:11434",
  "model": "qwen2.5:7b",
  "gws_command": "gws",
  "youtube_downloader_dir": "C:\\Users\\USER\\Documents\\workspace\\youtube-audio-downloader",
  "youtube_download_output_dir": null,
  "youtube_download_parallel": 3
  ,"tech_news_dir": "C:\\Users\\USER\\Documents\\workspace\\assist-chat\\release_runtime\\GetNewTechInfo"
  ,"tech_news_cli_exe": "C:\\Users\\USER\\Documents\\workspace\\assist-chat\\release_runtime\\GetNewTechInfo\\GetNewTechInfoCli.exe"
}
```

`youtube_download_output_dir` を `null` にすると、保存先未指定時はシステムの Downloads フォルダを使用します。

`youtube-audio-downloader` は assist-chat の Python 環境で不足している `PySide6` / `yt-dlp` / `demucs` を自動で確認し、必要なら `C:\Users\USER\Documents\workspace\youtube-audio-downloader\youtube_dl\venv` を作成して依存関係を入れます。これにより、アプリ起動時に個別に手動導入する手間を減らせます。

gwsがPATHにない環境では、`gws_command`に実行ファイルのフルパスを指定できます。OllamaはPATHから探さず、必ず`ollama\ollama.exe`を使います。
