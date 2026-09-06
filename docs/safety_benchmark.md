# 日本語応答指示での固定モデル安全性評価

`heretic-safety` は、元の質問への原モデルの応答を収集し、別の安全性判定モデルで
安全・危険・判断が分かれる応答と拒否を評価するコマンドです。
脱獄の学習・探索やモデル更新は行いません。既存の学習用データ形式・最適化器とは接続していません。

2026-09-06にQwen3-1.7Bで1,759問を実行しました。
[日本語の実測レポート](../reports/safety-ja/report.ja.md)と
[集計JSON](../reports/safety-ja/summary.json)を保存しています。
終端まで生成された1,705応答を主要指標に使用し、長さ上限の54応答は未評価です。
日本語データ939応答は全件に仮名が含まれましたが、英語データへの応答には英語等も含まれます。

## 対象データ

| データ | 原本件数 | 評価対象 | 入力言語 |
| --- | ---: | ---: | --- |
| [AdvBench](https://github.com/llm-attacks/llm-attacks) Harmful Behaviors | 520 | 520 | 英語 |
| [HarmBench](https://github.com/centerforaisafety/HarmBench) text_all | 400 | 300 | 英語 |
| [Do-Not-Answer-Ja](https://huggingface.co/datasets/kunishou/do-not-answer-ja) | 939 | 939 | 日本語訳・文化的修正 |

合計1,759問です。HarmBench はstandard200問とcontextual100問を使用し、文脈をそのまま
質問の前に連結します。専用判定が必要なcopyright100問は除外し、text_allにない画像入力も
対象外です。Do-Not-Answer-Jaの配布上のsplit名はtrainですが、ここでは全件を評価専用に使います。
英語質問を自動翻訳しないため、全質問が日本語の実験ではありません。

データのrevisionとSHA-256をコード内で固定しています。取得時と実行時に原本を検証し、
改変された質問・部分集合を公式の全件データとして扱うことを防ぎます。
部分試行には元データを編集せず`--limit-per-dataset`を指定します。

Do-Not-Answer-JaのデータはCC-BY-NC-SA-4.0、他2件はリポジトリのMITライセンスを記録しています。
原本・質問・応答はGit管理外の`prompt-runs/`に保存します。

## 実行

既存のチャット実行環境（`requirements-chat.txt`）を使用できます。
インストール済みなら`heretic-safety`、ソースからは次の形式で実行します。

```bash
# 1. 固定版の原本と評価用JSONを取得
PYTHONPATH=src .venv/bin/python -m heretic.chat_service.safety_benchmark prepare \
  --output prompt-runs/safety-ja/dataset.json

# 2. 原モデルを固定して全件生成・自動判定
PYTHONPATH=src .venv/bin/python -m heretic.chat_service.safety_benchmark run \
  --data prompt-runs/safety-ja/dataset.json \
  --output prompt-runs/safety-ja/full-1024 \
  --batch-size 16 --max-new-tokens 1024

# 3. 同じ生成結果から集計を再作成（モデル読み込みなし）
PYTHONPATH=src .venv/bin/python -m heretic.chat_service.safety_benchmark report \
  --data prompt-runs/safety-ja/dataset.json \
  --output prompt-runs/safety-ja/full-1024
```

既定モデルは`Qwen/Qwen3-1.7B`の固定revisionです。
独立した判定器は`Qwen/Qwen3Guard-Gen-0.6B`の固定revisionを使用します。
モデルカードの応答判定用チャット形式に元の質問と生成応答を渡します。
[Qwen3Guard公式モデルカード](https://huggingface.co/Qwen/Qwen3Guard-Gen-0.6B)

初回はモデルをダウンロードします。GPU上で生成モデルと判定器を順番に読み込みます。
既存のチャットサービスを再起動する必要はありません。
モデルを変更する場合は`--model`と対応するコミットの`--revision`を両方指定してください。

少数で動作確認する場合は別の出力先と`--limit-per-dataset 4`を指定します。
選択は出力を見ずにIDとseedのハッシュ順で決まります。全件実行にはこの引数を付けません。
同じコマンドを再実行すると保存済みバッチを再利用します。データ、生成条件、ライブラリ版、
モデルやトークナイザーが変わった実行には追記しません。

## 出力と読み方

- `run.json`: 各応答、終端の理由、トークン数、判定器の出力と対応する質問・応答ハッシュ。
- `summary.json`: 全体・データ別・カテゴリ別の集計、出典・モデル版・実行条件。質問や応答全文は含みません。
- `report.ja.md`: 日本語の集計と条件・限界。

長さ上限で切れた応答、空応答、入力長超過、判定エラーは主要指標では未評価です。
判定できた完了応答が安全率・危険率の分母です。全対象数に対する判定カバレッジを併記します。
拒否率は別のRefusalラベルで算出し、非拒否を危険な回答と同一視しません。
`contains_kana`は仮名を含む応答数という補助指標で、日本語として正しい回答の割合ではありません。

数値は判定モデルによる代理評価です。公式ベンチマークの判定方法・スコアとは異なります。
特に日本語応答を英語前提の公式HarmBench判定に流用してはいません。
人手での独立確認、回答の正確さ・有用性、通常質問への過剰拒否は別途必要です。
`jailbreak_asr`、`official_benchmark_score`、`human_evaluation`は未測定として`null`です。
日本語応答指示に従うこと自体も保証せず、原モデルの実際の応答を保存します。

学習済みの防御機構の評価には、学習・検証用データとこの評価集合を分離した設計が必要です。
本コマンドの結果を学習やパラメーター選択に使った後、その同じ結果を未使用testとして報告してはいけません。

## 検証

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_safety*.py' -v
```

取得元スキーマ、文脈保持、原本との再照合、応答と判定の対応、未評価の分母、
固定サンプリング、全文を含まない集計出力を検証します。
