# コタツ・ソフト

このリポジトリは、Discord Botベースの AI 社員システムと、ブラウザゲーム群 (ポータル含む) で構成されています。

## コンポーネント

- ai-core/
  - Discord Bot とコア制御ロジックを含む Python アプリケーション
  - AI社員 (PM、Dev、Marketing) をモジュール単位で管理
- game-projects/
  - コタツ・ソフト公式ポータルとゲーム群
  - index.html がポータルページ
  - 001_mikan_buster/ と 002_nyanko_dive/ がゲーム本体
- shared/
  - 仕様書やログなどの共通出力

## GitHub Pages 公開

このリポジトリには、game-projects/ 配下を GitHub Pages に自動デプロイするワークフローを追加しています。

### 1) 初回設定

1. GitHub のリポジトリ画面を開く
2. Settings > Pages を開く
3. Build and deployment の Source を GitHub Actions に設定

### 2) デプロイ

- main ブランチに push すると自動デプロイされます
- 手動実行する場合は Actions タブから Deploy game-projects to GitHub Pages を実行します

### 3) 公開 URL

- 通常は https://<GitHubユーザー名>.github.io/<リポジトリ名>/
- 公開後、トップページとして game-projects/index.html が表示されます

## ai-core セットアップ

1. ai-core/ に移動

```bash
cd kotatsu-soft/ai-core
```

2. Python 仮想環境を作成して有効化

```bash
python -m venv venv
source venv/bin/activate   # macOS/Linux
venv\Scripts\activate     # Windows PowerShell
```

3. 依存関係をインストール

```bash
pip install -r requirements.txt
```

4. .env.example をコピーして環境変数を設定

```bash
copy .env.example .env
```

5. Bot を起動

```bash
python src/main.py
```

## 補足

- ai-core/ は Discord Bot の起動と AI 制御を担います
- game-projects/ はゲーム画面とポータルを管理します
- shared/ は仕様書やログなどの共通出力を格納します

## 仕様書とゲームの紐づけ管理

ai-core が Go 判定後に仕様書を生成すると、`shared/specs/spec_game_links.json` に自動登録されます。

- 仕様書生成: `shared/specs/spec_*.md` を出力
- レジストリ更新: `shared/specs/spec_game_links.json` に記録追加
- ポータル反映: `game-projects/index.html` はレジストリを読み、`data-game-id` ごとに最新仕様書リンクを表示

### 紐づけを更新する手順

1. 仕様書を生成（Discord の Go）
2. 必要なら手動でゲームIDへ紐づけ

```bash
cd kotatsu-soft/ai-core
python scripts/link_spec_to_game.py --spec spec_xxx.md --game-id mikan_buster --game-path game-projects/001_mikan_buster/src/index.html --game-title "10秒コタツミカンバスター"
```

主要な `game-id` 例:

- `mikan_buster`
- `nyanko_dive`
