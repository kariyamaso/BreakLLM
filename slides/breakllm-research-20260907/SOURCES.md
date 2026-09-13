# Sources & asset provenance

## デザインの参照

- Yusuke Uchida (@yu4u), 「Swin Transformer (ICCV'21 Best Paper) を完璧に理解する資料」, 2021-12-12.
  https://speakerdeck.com/yu4u/swin-transformer-iccv21-best-paper-wowan-bi-nili-jie-suruzi-liao
  閲覧: 2026-09-07。
  表紙の2:1配置、細い基準線、白地、フッターバー、図へのCyan注釈を参考にした。
  38ページのPDFを作業用ディレクトリで視覚確認した。資料そのもの、著者の本文、DeNA/MoTロゴ、原論文図はこのアセットに含めていない。
  元資料の著作権は権利者に帰属する。本テンプレートは同資料の公式テンプレートではない。

- ユーザー資料 `未踏_906MTG.pdf` と既存HTML:
  `/Users/kariyamaso/workspace/research/SilentSense/platform/runtime/web/report/906/template.css`
  `/Users/kariyamaso/workspace/research/SilentSense/platform/runtime/web/report/906/layouts.js`
  配色、Noto Sans JP、フロー・表・数式・トークンの扱い、S.add形式を継承。
  元ファイルは変更していない。

## 同梱アセット

| ファイル | 由来・用途 |
|---|---|
| assets/fonts/NotoSansJP.ttf | 既存906MTGから複製。SIL Open Font License 1.1 |
| assets/fonts/OFL.txt | フォントライセンス原文 |
| assets/monitor-example.png | 自作SilentSenseの記録画面。platform/docs/monitor-person-restored.png、2026-09-06 |
| assets/monitor-detail.png | 上記の3D領域を切り出し。色や人物の追加・合成なし |
| assets/architecture.svg | 本テンプレート用の独自・編集可能な構成図見本 |
| assets/token-groups.svg | 本テンプレート用の独自・編集可能な要素対応図見本 |

Monitor画像の入力はMM-Fiの記録再生。部屋は参照モデルで、人体・電波点群は姿勢・CSI由来の描画。実機展開の性能証明には使用しない。

## 再利用・配布の注意

HTML/CSS/JSと独自SVGは今回作成したユーザー用アセット。外部資料の再配布権は付与しない。
フォントを再配布する際はOFL.txtを保持する。
アプリ画像は個人のテンプレート見本として同梱している。外部配布や公開時は、プロジェクト・データセットの権利と表示内容を確認し、必要なら別の画像に差し替える。
