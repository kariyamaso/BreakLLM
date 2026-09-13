# Kariyama Research — HTML Slide Template

研究・開発報告、輪講、技術解説のための再利用アセットです。
Speaker Deck の Swin Transformer 解説資料の構成を参考に、906MTG の配色・矩形・フロー・数式枠を組み合わせました。元資料の本文・ロゴ・論文図は同梱していません。

## すぐに見る

- [18種類のレイアウト](gallery.html)
- [新規資料のひな形（5ページ）](starter.html)
- [デザイン仕様](DESIGN.md)
- [出典・ライセンス](SOURCES.md)

この複製では `index.html` が作成中の資料、`gallery.html` がレイアウト見本です。`starter.js` を編集します。

HTML をブラウザで直接開きます。ビルド、npm install、アプリ起動、ネット接続は不要です。フォルダ内の CSS・JS・assets を一緒に保持してください。

これは `workspace/agent` に保存したファイルベースのテンプレートです。Codex の個人テンプレートギャラリーやプラグインへの登録は行っていません。既存の SilentSense スライド・アプリも変更していません。

## 新しい資料を作る

テンプレート原本を残し、新しいフォルダへひな形を複製します。

```sh
cd /Users/kariyamaso/workspace/agent/assets/slide-templates/kariyama-research
node tools/new-deck.mjs /Users/kariyamaso/workspace/research/new-talk
```

保存先の親ディレクトリは事前に用意してください。保存先が存在する場合は上書きせず停止します。
生成された `index.html` を開き、`starter.js` を編集します。生成資料の `index.html` はレイアウト一覧ではなく、5ページのひな形です。
レイアウト見本は `gallery.html`、内容は `layouts.js` として同じフォルダに残ります。デザイン仕様・フォント・検証ツールも一緒に複製します。

```js
S.brand = 'KARIYAMA';
S.title = '研究・開発報告';

S.add('工夫点',
  S.flow([
    ['入力', '振幅・位相'],
    ['表現', '実装している変換'],
    ['推定', 'タスク出力', 'mint']
  ]) + S.take('既存構成から変更した点'),
  {
    status: '設計・未評価',
    source: '実装・設計資料への参照',
    notes: '詳しい説明、条件、制約を記載する。'
  }
);
```

元の `layouts.js` から必要な `S.add(...)` を追加してください。本文はローカルで編集する HTML です。外部データやユーザー投稿の HTML を直接渡す用途には使いません。

### 画像と出典

```js
S.add('アプリ画面',
  S.fig('assets/my-screen.png',
    '画面名 / 撮影日 / 入力データの種類',
    {kind: 'app', alt: '画面の内容を簡潔に説明'}),
  {source: '版・commit・収録条件', notes: '説明する操作'}
);
```

論文図は利用者が原論文から適切に用意し、著者・年・Fig.番号・URLを付けます。図の比率を維持し、原図と独自の拡張を区別してください。

### 性能グラフ

```js
S.add('性能比較',
  S.bars(['条件A', '条件B'], [
    {name: '比較手法', color: 'var(--comparison)', values: [null, null]},
    {name: '変更後', color: 'var(--brand-blue)', values: [null, null]}
  ], {title: 'Accuracy', max: 100, unit: '%'}),
  {status: '未評価', source: '実施後に分割・seed・結果ファイルを記載'}
);
```

`null` は「未測定」と表示します。ゼロに置き換えません。
一覧のグラフ数値はすべて見本です。新規資料のひな形には架空の性能値を入れていません。
誤差棒・信頼区間が必要な場合は実測データに合わせて追加し、平均値だけで結論を作らないでください。

## 18レイアウト

表紙 / 概要 / セクション / アーキテクチャ / 論文図＋説明 / 比較 / トークン・マスク / 数式・損失 / 性能比較 / Ablation / 計測フロー / アプリ画面 / 画面詳細 / コード / 評価条件 / 配色・部品 / 図中心 / まとめ。

対応ページと選び方は [layouts.json](layouts.json) に保存しています。

## 配色と編集ファイル

| ファイル | 役割 |
|---|---|
| `theme.css` | 濃紺・青・Cyan・Mint・赤、フォント、サイズ |
| `slides.css` | 16:9、罫線、帯、図形、図表、印刷 |
| `components.js` | 表・グラフ・フロー・注釈の作成関数 |
| `layouts.js` | 18種類の編集可能な見本 |
| `starter.js` | 新しい資料の内容 |
| `viewer.js` | ページ移動・一覧・ノート・全画面 |
| `assets/` | フォント、編集可能なSVG、実画面例 |
| `AGENTS.md` | 今後AIに依頼するときの作成ルール |
| `tools/new-deck.mjs` | 上書きを拒否する複製ツール |
| `tools/verify.mjs` | ブラウザ・PDF・はみ出し検証 |

見出しと本文は Noto Sans JP。等幅はコード・データだけに使用します。
独立した SVG ファイルには配色が含まれるので、テーマを大きく変える場合は SVG 内の色も合わせます。

## 操作とPDF

- ← / →、PageUp / PageDown：移動
- Home / End：最初 / 最後
- G：一覧、N：ノート、F：全画面、P：印刷
- 一覧のスライドは Tab → Enter でも選択可能
- `#slide-9` のようなURLで指定ページを開けます

PDFはブラウザの「PDF / 印刷」から作成します。

1. 保存先を「PDFに保存」にする。
2. 背景のグラフィックを有効、ブラウザのヘッダー・フッターを無効にする。
3. CSS指定の 16:9・余白なしを使用する。用紙サイズを別の規格で上書きしない。

一覧表示中でも全ページを順に出力します。操作バー・ノートは印刷しません。
図表・本文は HTML/SVG なので、PDFでも文字と線が保たれます。アプリ画面・論文画像は元画像の解像度に依存します。

## 今後AIへ渡す指示例

> /Users/kariyamaso/workspace/agent/assets/slide-templates/kariyama-research の README.md と DESIGN.md を読み、このテンプレートを複製してHTMLスライドを作成してください。原本は変更せず、本文・図・結果を差し替え、全ページの描画とPDF印刷を検証してください。

## 検証

通常表示に依存ライブラリはありません。検証のみ Node.js と任意の Playwright 環境を使います。
Nodeテストのページ数・文言チェックは原本用です。資料の内容を変更した後は必要に応じてテストを更新してください。ブラウザ検証は `starter.js` のページ追加に対応し、複製先では見本（gallery）と作成中の資料の両方を描画します。

```sh
node --test tests/template.test.mjs
# Playwrightを解決できる環境で:
node tools/verify.mjs --output /absolute/path/to/qa-output
```

既存 Playwright のパスは `PLAYWRIGHT_MODULE`、既存 Chrome 実行ファイルは `CHROME_BIN` で指定できます。検証ツールはブラウザを自動ダウンロードしません。
作成した資料は改めて検証してください。原本の検証記録やプレビューは複製していません。
