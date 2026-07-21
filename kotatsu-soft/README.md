# コタツ・ソフト

このリポジトリは、Discord Botベースの「AI社員システム」と、AIが生成・開発する「Webゲーム群およびポータルサイト」という2つの異なるコンポーネントで構成されています。

## コンポーネント

- `ai-core/`
  - Discord Botとコア制御ロジックを含むPythonアプリケーション
  - AI社員（PM、Dev、Marketing）をモジュール単位で分割して管理します
- `game-projects/`
  - 「コタツ・ソフト」公式ポータルサイトとゲーム群のワークスペース
  - `index.html` がポータルサイト、`001_mikan_buster/` が第1弾ゲーム作品です
- `shared/`
  - 会社共通の仕様書やシステムログを格納します

## セットアップ

1. `ai-core/` に移動します

```bash
cd kotatsu-soft/ai-core
```

2. Python仮想環境を作成して有効化します

```bash
python -m venv venv
source venv/bin/activate   # macOS/Linux
venv\Scripts\activate    # Windows PowerShell
```

3. 依存関係をインストールします

```bash
pip install -r requirements.txt
```

4. `.env.example` をコピーして環境変数を設定します

```bash
copy .env.example .env
```

5. Botを起動します

```bash
python src/main.py
```

## 注意

- `ai-core/` はDiscord Botの起動とAI制御を担います。
- `game-client/` はゲームの画面表示、アセット、ビルド成果物を管理します。
- `shared/` は仕様書やログなどの共通出力を格納します。
