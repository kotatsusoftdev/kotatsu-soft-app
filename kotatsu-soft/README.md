# コタツ・ソフト

このリポジトリは、Discord Botベースの「AI社員システム」と、AIが生成・開発する「Webゲームプログラム」という2つの異なるコンポーネントで構成されています。

## コンポーネント

- `ai-core/`
  - Discord Botとコア制御ロジックを含むPythonアプリケーション
  - AI社員（PM、Dev、QA、Asset）をモジュール化して管理します
- `game-client/`
  - AIが生成するWebゲームのHTML/CSS/JavaScriptソース
  - 生成されたゲームアセットとビルド成果物を保持します
- `shared/`
  - PM AIが作成する仕様書の出力先
  - システムログやスタックトレースの保存先

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
