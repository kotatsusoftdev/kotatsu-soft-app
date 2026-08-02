# 画像プリセットライブラリ

ゲーム実装で利用するフリー素材のローカルライブラリです。  
実体（画像ファイル）は Git / GitHub Pages に載せません。AI 選択用のメタデータだけをリポジトリに残します。

## ディレクトリ

```
asset-presets/
├── README.md           # 本ファイル
├── ATTRIBUTIONS.md     # 出典・ライセンス一覧（コミット可）
├── catalog.json        # AI / 人手選択用メタデータ（コミット可）
└── files/              # 素材実体（.gitignore。公開しない）
    ├── characters/
    ├── backgrounds/
    ├── ui/
    └── effects/
```

## 運用

1. フリー素材をダウンロードし、利用規約を確認する（再配布不可のものは置かない）
2. `files/{category}/` に配置する
3. `catalog.json` にエントリを追加する（`id` / `path` / `category` / `tags` / `license` / `attribution` / `source_url` など）
4. `ATTRIBUTIONS.md` に出典を追記する
5. ゲームで使うときだけ、採用分を `game-projects/{NNN}_{slug}/assets/` へコピーし、`src/index.html` から相対参照する

ポータル用サムネ・ブランド画像は従来どおり `game-projects/assets/` です。本ライブラリとは混ぜません。

## 公開方針

| 対象 | Git |
|------|-----|
| `files/` 配下の画像実体 | 無視（プリセット全集は公開しない） |
| `catalog.json` / `ATTRIBUTIONS.md` | コミット可 |
| ゲームに採用した画像のみ | `game-projects/{NNN}_{slug}/assets/` にコピーしてコミット |

詳細は [GAME_DEV_PROCESS.md](../GAME_DEV_PROCESS.md) の画像プリセット節を参照してください。
