# コタツ・ソフト

このリポジトリは、Discord Botベースの AI 社員システムと、ブラウザゲーム群 (ポータル含む) で構成されています。

## コンポーネント

- ai-core/
  - Discord Bot とコア制御ロジックを含む Python アプリケーション
  - AI社員 (PM、Dev、Marketing) をモジュール単位で管理
- game-projects/
  - コタツ・ソフト公式ポータルとゲーム群
  - index.html がポータルページ
  - 001_matatabi_chaos/ がゲーム本体
- shared/
  - 仕様書・議事録・プレイテストレビュー・ログなどの共通出力
  - review/ にテストプレイ指摘と修正履歴（`review_*.md`）を保管

## 関連ドキュメント

- [ゲーム開発プロセス設計書](./GAME_DEV_PROCESS.md) … 企画会議から反省会までのライフサイクル・規約の正本

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
- shared/ は仕様書・議事録・プレイテストレビュー（`shared/review/`）・ログなどの共通出力を格納します

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
python scripts/link_spec_to_game.py --spec spec_xxx.md --game-id matatabi_chaos --game-path game-projects/001_matatabi_chaos/src/index.html --game-title "マタタビ大合唱 ～モフモフ・カオス・タワー～"
```

主要な `game-id` 例:

- `matatabi_chaos`

## 開発完了後の自動反省会（教訓更新）

仕様・会議ログ・レビュー・完成コードを読み、各 AI 社員の教訓（1〜2文×3個）を進化させます。
レビュー指摘を最優先し、具体→抽象化した再発防止則へ更新します（言い換えのみは禁止）。
更新された教訓は次の企画会議の system instruction に自動で入り、テーマ非依存の原則として発言に反映されます（箇条書きの読み上げはしません）。

- 出力先（`config.yaml` と同じ配置）:
  - `ai-core/src/agents/pm/lessons_learned.yaml`
  - `ai-core/src/agents/dev/lessons_learned.yaml`
  - `ai-core/src/agents/marketing/lessons_learned.yaml`

### 実行手順

1. 仕様書・会議ログ・レビューが同じ `artifact_stem` で揃っていること
2. できれば `spec_game_links.json` でゲーム本体へ紐づいていること（未紐づけなら `--game-path` を指定）
3. `ai-core/.env` に `GEMINI_API_KEY` があること

```bash
cd kotatsu-soft/ai-core
# venv 推奨（スクリプトは venv があれば自動で切り替えます）
.\venv\Scripts\python.exe scripts/post_mortem.py --artifact-stem "テトリスと猫を掛け合わせたゲームを作って_20260725_124622"
```

保存せず差分だけ見る場合:

```bash
.\venv\Scripts\python.exe scripts/post_mortem.py --artifact-stem "テトリスと猫を掛け合わせたゲームを作って_20260725_124622" --dry-run
```

仕様ファイル名から起動する場合:

```bash
.\venv\Scripts\python.exe scripts/post_mortem.py --spec "spec_テトリスと猫を掛け合わせたゲームを作って_20260725_124622.md"
```

### Discord 社長命令チャンネルから起動

1. Discord の社長命令チャンネル（`.env` の `PRESIDENT_ORDER_CHANNEL_ID`）に何かメッセージを送る
2. Bot が「企画会議 / 反省会」の選択肢を出すので、**反省会** を選ぶ
3. 社長命令チャンネルに短い誘導が出るので、反省会チャンネル（`POST_MORTEM_CHANNEL_ID`）へ移動する
4. 反省会チャンネルで直近リンク済みゲームの提案を確認し、`はじめる` を押す
5. 同チャンネルに各 AI 社員（すずかちゃん / スゴ杉くん / ヂャイアン）名義で教訓 before/after が投稿される

※ `artifact_stem` の手入力は不要です（`spec_game_links.json` の直近リンクから自動解決）。
※ 企画会議も社長命令チャンネルから選べます。テーマは Modal で入力し、途中経過・Go/NoGo は企画検討チャンネル（`MEETING_CHANNEL_ID`）に出ます。
※ 社長命令チャンネルはプロセス選択専用です。経過・結果は各プロセスのチャンネルに出ます。
