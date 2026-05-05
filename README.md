# Eagle Viewer — AndroidからEagleライブラリをWi-Fi経由で閲覧
<img alt="hogehoge" src="https://github.com/user-attachments/assets/7557cc35-98ae-440f-ae59-b2532c1a63ec" width="30%">
<img alt="hogehoge" src="https://github.com/user-attachments/assets/4ff11c9e-00f4-491b-9122-79c0bbfc4297" width="30%">

## はじめに

**Eagle Viewer** は、PC上で動作する画像管理ソフト [Eagle](https://eagle.cool) のライブラリを、**Android のブラウザから LAN 経由で閲覧・管理できる**Webビューアです。

外出先からVPN経由でアクセスしたり、ソファに寝ながらスマホで画像整理したりと、様々なシーンで活用できます。

---

## 必要なもの

- **PC**（Windows）に Eagle がインストール済みであること
- **Android スマートフォン**（Chrome または Firefox 推奨）
    ※iOSでも動作すると思いますが未検証です
- **Python 3.x**（PC にインストール済みであること）
   pythonインストール(公式) https://www.python.jp/install/windows/install.html
- PC と Android が **同じ Wi-Fi** に接続していること（または VPN）

---

## セットアップ手順

### 1. ファイルを準備する

GitHubから以下のファイルをダウンロードし、同じフォルダに置きます。

```
eagle_proxy.py
eagle-viewer.html
eagle_tag_normalizer.py
eagle_tag_normalizer.html
config.ini.example
start_eagle_proxy.bat
```

### 2. 設定ファイルを作成する

`config.ini.example` をコピーして `config.ini` にリネームし、編集します。

```ini
[eagle]
port = 41595
token = YOUR_EAGLE_V2_API_TOKEN  ← Eagle の設定画面で確認

[server]
port = 8080
html_file = eagle-viewer.html
```

**Eagle V2 API トークンの確認方法：**
Eagle アプリ → 設定 → 開発者 → API トークン

### 3. Pillow をインストールする（任意）

サムネイルをWebP形式に圧縮して通信量を削減したい場合：

```
pip install Pillow
```

### 4. プロキシを起動する

`start_eagle_proxy.bat` をダブルクリックすると、コンソールに以下が表示されます。

```
Eagle Proxy 起動中 → ポート 8080
AndroidのChromeで開く: http://192.168.0.xxx:8080/
停止: Ctrl+C
```

### 5. Android から接続する

表示された URL（例：`http://192.168.0.107:8080/`）を Android のブラウザで開きます。

---

## 画面の説明

### 一覧画面
<img alt="hogehoge" src="https://github.com/user-attachments/assets/7557cc35-98ae-440f-ae59-b2532c1a63ec" width="30%">

| 要素 | 説明 |
|---|---|
| 検索バー | 名前またはタグで全文検索。Enter キーで実行 |
| ☆ フィルタ | Eagle の星評価（1〜5）で絞り込み |
| 作成日↓ | ソート順の切替 ※現在機能しません  |
| 🖼 ボタン | スマホの画像をアップロードして類似検索<br> ※EagleにAI searchプラグインの導入と、事前のライブラリ解析が必要です。[詳細](https://jp.eagle.cool/support/article/ai-search)|
| 📁 ボタン | フォルダ・スマートフォルダで絞り込み |
| ⟳ ボタン | Eagle を再起動してタグを再収集 |
| 件数表示 | 右上に現在の検索結果件数を表示 |

**タグチップ検索：**
検索バーにタグ名を入力してサジェストから選択すると、タグのチップが追加されます。複数チップは AND 検索になります。`-タグ名` で除外検索も可能です。

### 詳細画面
<img alt="hogehoge" src="https://github.com/user-attachments/assets/4ff11c9e-00f4-491b-9122-79c0bbfc4297" width="30%">

| 要素 | 説明 |
|---|---|
| ⤢ ボタン | フル解像度表示に切替 |
| 🔍 類似画像 | AI による類似画像検索 |
| ⬇ ダウンロード | 元ファイルをダウンロード |
| ‹ › ボタン | 前後の画像に移動 |
| ★ 評価 | Eagle の星評価を設定（1〜5） |
| 作成/更新日 | ファイルの日付情報 |
| タグ一覧 | タグをタップして同タグを検索、× で削除 |
| タグ追加 | 入力欄からタグを追加（サジェスト対応） |

**スワイプ操作：**
- 左右スワイプで前後の画像に移動
- ドラッグで画像を動かすことも可能

### フル解像度表示

画像をタップするとフル解像度を読み込み、拡大表示モードに切り替わります。

| 操作 | 動作 |
|---|---|
| タップ / スワイプ | 詳細画面に戻る |
| ピンチアウト | ズームイン（最大8倍） |
| ピンチイン | ズームアウト |
| 1本指ドラッグ（ズーム中） | 画像を移動 |
| ダブルタップ | タップ位置を中心に2倍ズーム |

---

## タグ正規化ツール

`{{sky}}` や `[sky]` のような括弧付きタグを一括で正規化するツールも同梱しています。

アクセス URL：`http://192.168.0.xxx:8080/tag-normalizer`

1. **プレビュー実行** で変更内容を確認
2. 問題なければ **一括適用** で反映

---

## 注意事項

- このツールは **ローカルネットワーク専用** です。外部公開する場合は適切なセキュリティ対策を行ってください
- Eagle API の利用は [Eagle の利用規約](https://developer.eagle.cool) に従います
- `config.ini` には API トークンが含まれるため、GitHub 等に公開しないようにしてください（`.gitignore` 設定済み）

---

## 動作環境

| 項目 | 内容 |
|---|---|
| PC OS | Windows 10 / 11 |
| Python | 3.8 以上 |
| Eagle | v3.0 以上（V2 API 対応） |
| スマホ | Android Chrome / Firefox 推奨 |

---

## ライセンス

MIT License

---

*Eagle は [eagle.cool](https://eagle.cool) が提供する画像管理ソフトウェアです。このツールは非公式の外部ツールです。*
