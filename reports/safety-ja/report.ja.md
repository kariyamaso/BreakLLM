# 日本語応答指示での固定モデルの安全性評価

状態: `complete`。作成日時（UTC）: 2026-09-06T06:20:56.147990+00:00

元の質問に対する原モデルの応答を、別の安全性判定モデルで自動評価した結果です。
脱獄の学習・探索、モデル更新は実施していません。公式ASRや人手で確認した安全率ではありません。

対象モデル: `Qwen/Qwen3-1.7B`。判定モデル: `Qwen/Qwen3Guard-Gen-0.6B`。
AdvBench/HarmBench は英語原文入力、Do-Not-Answer-Ja は公開された日本語入力です。
全入力が日本語の実験ではありません。既存チャットと同じ日本語応答指示を使用しています。

| データ | 対象 | 完了応答 | 安全判定 | 危険判定 | 判断が分かれる | 未評価 | 長さ上限 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| advbench | 520 | 511 | 481 | 28 | 2 | 9 | 9 |
| harmbench | 300 | 269 | 173 | 71 | 25 | 31 | 31 |
| do_not_answer_ja | 939 | 925 | 854 | 44 | 27 | 14 | 14 |

未評価には未生成・空応答・長さ上限で途切れた応答・判定形式エラーを含みます。
安全/危険の率は、終端まで生成され、判定できた応答を分母とします。未評価は安全に数えません。

| データ | 判定カバレッジ | 自動安全判定率 | 自動危険判定率 | 自動拒否判定（件数/分母） |
| --- | ---: | ---: | ---: | ---: |
| advbench | 98.3% | 94.1% | 5.5% | 477/511 |
| harmbench | 89.7% | 64.3% | 26.4% | 137/269 |
| do_not_answer_ja | 98.5% | 92.3% | 4.8% | 327/925 |

拒否しないことと危険な回答をすることは別の判定です。

## 評価条件と限界

- 決定的生成、最大1024トークン、thinking無効、ツール無効。
- 途中で切れた応答への判定は生データに保存しますが、主要指標から除外します。
- 判定器の誤分類は残ります。人による独立評価、正確性・有用性・過剰拒否の測定は未実施です。
- 日本語の翻訳・文化的修正を含むデータと英語入力の結果を言語間の公平な比較とは扱いません。
- 入力・応答・判定はSHA-256で対応付けています。集計には質問や応答の全文を含めません。
- 学習やパラメーター選択に使っていない固定評価です。最適化前後の比較はありません。

## 出典

- [advbench](https://raw.githubusercontent.com/llm-attacks/llm-attacks/098262edf85f807224e70ecd87b9d83716bf6b73/data/advbench/harmful_behaviors.csv): revision `098262edf85f807224e70ecd87b9d83716bf6b73`、原本520件、採用520件、除外 `{}`、ライセンス `MIT (repository)`。
- [harmbench](https://raw.githubusercontent.com/centerforaisafety/HarmBench/8e1604d1171fe8a48d8febecd22f600e462bdcdd/data/behavior_datasets/harmbench_behaviors_text_all.csv): revision `8e1604d1171fe8a48d8febecd22f600e462bdcdd`、原本400件、採用300件、除外 `{"copyright": 100}`、ライセンス `MIT (repository)`。
- [do_not_answer_ja](https://huggingface.co/datasets/kunishou/do-not-answer-ja/resolve/8885330ded1853396458e8565c28ac62d7110281/dna_ja_1k.json): revision `8885330ded1853396458e8565c28ac62d7110281`、原本939件、採用939件、除外 `{}`、ライセンス `CC-BY-NC-SA-4.0`。
- [Qwen3Guard モデルカード](https://huggingface.co/Qwen/Qwen3Guard-Gen-0.6B)。
