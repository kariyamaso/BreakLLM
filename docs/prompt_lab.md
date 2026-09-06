# Prompt Lab: 学習トークンとプロンプト構成の実験

## 調査結果

2026-09-06 に公式リポジトリ・論文を確認。作業開始時のローカル HEAD は
`3521f8648a0dccf6e12a92666862632235fac7e6`。

**Heretic 本体とは別に、ご質問に近い技術は既に存在する。**
Heretic の中心は、拒否に関係する残差方向を求め、層の射影行列への介入を
Optuna で調整する方式。一般的な勾配によるモデル全体の fine-tuning とも異なる。
確認した公式コードと作業開始時のローカルコードには、学習可能な入力トークンの
最適化機能は見当たらなかった。全ての派生版や未公開開発についての不存在の主張ではない。
[Heretic 公式](https://github.com/p-e-w/heretic)

| 系統 | 学習・探索する対象 | 必要なアクセス | 関連研究 |
| --- | --- | --- | --- |
| Soft Prompt | 入力に追加する連続埋め込み。モデル本体を固定可能 | 埋め込み入力と勾配 | [SOS! (2024)](https://arxiv.org/abs/2407.03160)、[Soft Prompt Threats (NeurIPS 2024)](https://papers.neurips.cc/paper_files/paper/2024/hash/10f34ee79b62627b7ebf6279d35ea480-Abstract-Conference.html) |
| 内部表現に基づく Soft Prompt | 拒否された文脈と受容された文脈の概念表現の差 | 内部表現と勾配 | [MSE-Break (ICML 2025 Actionable Interpretability Workshop)](https://icml.cc/virtual/2025/49573) |
| 離散トークン探索 | 実際の語彙に属するサフィックス | 探索時に勾配等。生成結果は文字列 | [GCG (2023)](https://arxiv.org/abs/2307.15043) |
| 自然言語の自動構成 | 読めるプロンプトの生成・更新 | 方式ごとに異なる。PAIR はテキストの問い合わせで動作 | [PAIR (2023)](https://arxiv.org/abs/2310.08419)、[AutoDAN (ICLR 2024)](https://arxiv.org/abs/2310.04451) |

MSE-Break は、内部の拒否を引き起こす表現を Soft Prompt で変えるという点で、
ご質問の発想に特に近い。ただし上記リンクはワークショップ発表であり、
ICML 本会議の採択論文として扱ってはいけない。

「Learnable Token」には少なくとも二つの意味がある。連続埋め込みは任意の実数
ベクトルで、通常のテキスト API にそのまま送れない。離散トークンは語彙の ID
なので文字列として扱えるが、トークナイザーや再トークン化で結果が変わりうる。
連続埋め込みを最近傍の単語に置き換えても効果は保存されるとは限らない。
[Hugging Face: Soft prompts](https://huggingface.co/docs/peft/main/en/conceptual_guides/prompting)

モデル内部の拒否、外部の入力分類器、出力フィルターは異なる評価対象。
内部へのアクセスを持つ実験の成功だけでは、テキスト API のガードレールを
通過する証拠にならない。また、正当な依頼の過剰拒否を減らす研究と、
安全境界を越える応答を誘発する研究は、データと成功条件を区別する必要がある。
過剰拒否の評価例として [XSTest](https://arxiv.org/abs/2308.01263) がある。

## この実装でできること

`heretic-prompt` は既存の `heretic` コマンドから独立して動作する。

- `train`: 固定した causal LM に対して Soft Prompt を学習し、validation の
  損失が最小の状態を保存する。初期状態も選択候補に含む。
- `search`: JSON に記載した自然言語の prefix/suffix 候補を対象モデルで評価し、
  validation の目的応答損失が最小のテンプレートを選択する。
- `evaluate`: 未使用の test データで原文、選択したテキスト、Soft Prompt を
  比較し、生成応答と指標を JSON に保存する。
- `generate`: 保存したアーティファクトを読み込んで応答を生成する。
- `render`: テキストのアーティファクトからプロンプトを構成する。モデルの
  読み込みや外部 API 呼び出しは不要。

これは **再現可能な研究ベースライン**。上記論文の全手法の再実装でも、
特定モデルで jailbreak が実証済みのシステムでもない。`search` は有限個の
候補から選択する方式で、LLM による反復的な文章生成や GCG は実装していない。
外部のガードレールに「抵触しない」ことを保証する機能もない。

初期対応は単一デバイス上の非量子化・テキスト専用・decoder-only Transformers
モデル。CPU/CUDA/MPS を選べるが、この変更の動作検証は CPU で実施した。
モデルのダウンロード、実際の対象モデルを使う大規模学習、外部サービスに対する
評価は、この変更の検証には含まれない。

## 実行

リポジトリの依存関係は `uv` で用意する。以下の `MODEL_PATH` を使用するモデルの
ローカルディレクトリに置き換える。Hub のモデル ID も指定可能で、その場合は
キャッシュがなければダウンロードされる。厳密な比較には `--revision` で
コミットを固定する。オフラインでは `--local-files-only` を追加する。

```sh
uv run heretic-prompt --help

# 1. テキストの構成候補を選ぶ
uv run heretic-prompt search \
  --model MODEL_PATH \
  --data examples/prompt_lab/data.jsonl \
  --templates examples/prompt_lab/templates.json \
  --output prompt-runs/text-01

# 2. モデル本体を固定して追加ベクトルだけ学習する
uv run heretic-prompt train \
  --model MODEL_PATH \
  --data examples/prompt_lab/data.jsonl \
  --tokens 16 --epochs 10 --learning-rate 0.01 \
  --output prompt-runs/soft-01

# 3. 学習・選択に使用していない test split で比較する
uv run heretic-prompt evaluate \
  --artifact prompt-runs/soft-01 \
  --data examples/prompt_lab/data.jsonl \
  --max-new-tokens 32 \
  --output prompt-runs/evaluation-01

# 4. 保存した Soft Prompt を適用する
uv run heretic-prompt generate \
  --artifact prompt-runs/soft-01 \
  --prompt 'What is six plus three?' --max-new-tokens 32

# 5. 選択したテンプレートを通常の文字列として使用する
uv run heretic-prompt render \
  --artifact prompt-runs/text-01 \
  --prompt 'What is six plus three?'
```

既に必要な依存関係のある Python 環境では
`PYTHONPATH=src python3 -m heretic.prompt_lab --help` でも起動できる。
古い `uv` で既存の `exclude-newer = "7 days"` 設定を解釈できない場合にも、
この形式で本機能を直接実行できる。

`train` に `--template-artifact prompt-runs/text-01` を追加すると、
選択したテキスト構成と Soft Prompt を併用する。`evaluate` はこの場合
原文、テキストのみ、テキスト＋Soft Prompt の比較を出力する。
テキストだけを比較するには `evaluate --artifact prompt-runs/text-01` を使う。
identity が最良だった場合は原文と同一なので、重複する比較行を出さない。

`--dtype` の初期値は `float32`。大きい GPU モデルには対応状況を確認して
`--dtype bfloat16` などを選ぶ。`--device` は `auto/cpu/cuda[:N]/mps`。
Soft Prompt だけの学習でも、固定した本体を通る逆伝播の活性値にはメモリが必要。
`--batch-size` は勾配を蓄積する例数であり、同時に処理するパディング付きバッチ数ではない。

チャット形式はトークナイザー付属のテンプレートを用い、必要なら `--system` を指定。
チャット形式のないベースモデルには明示的に `--plain` を指定する。
学習時の system/plain/max-length は保存され、推論時にも同じ設定を使う。
テンプレートを引き継ぐ学習でも同じ設定を指定する。

## データと目的関数

入力 JSONL の各行は次の四つの文字列フィールドからなる。

```json
{"id":"example-1","prompt":"What is two plus two?","target":"4","split":"train"}
```

`split` は `train`、`validation`、`test`。`target` は望む応答の本文または
継続部分で、質問を含めない。配布例は演算の動作確認用であり、jailbreak の
学習データや安全性ベンチマークではない。

Soft Prompt を P、固定モデルを θ、目的応答を y、入力を x とすると、
実装の目的関数は次の教師あり損失。

```text
L(P) = mean_examples mean_target_tokens [-log pθ(y_t | P, chat(x), y_<t)]
```

- P は `[追加トークン数, 埋め込み次元]` の連続テンソル。
- P は BOS を含むシリアライズ済みチャット全体の前に追加する。
  system/user/assistant の内容や境界を文字列で書き換えない。
- 本体の全パラメーターを固定し、AdamW は P のみを更新する。
- 指定した target のトークンだけを採点する。入力の再現やパディングを
  学習しない。EOS や応答の終端は target の後に自動追加しない。
- まず例内のトークン平均、次に例間平均を取る。長い target だけに重みが偏らない。
- 初期ベクトルは `--init-text` の語彙埋め込みを繰り返し／切り詰めて作る。
  `--seed` は学習順序に適用する。異なるハードウェア間のビット単位一致は保証しない。
- 拒否キーワードが消えたこと、guard モデルの判定、内部残差方向は現在の
  最適化目的に含めていない。

完全な応答を生成するテストでは、目的応答の教師強制を一切行わない。
`target_nll` が改善しても生成成功を保証しない。短い肯定的な書き出しだけの
target は、その後の拒否や無関係な応答を見落とすので、研究の成功判定に使わない。

## 出力と検証

学習／探索ディレクトリには `artifact.json`、Soft Prompt の場合は追加で
`soft_prompt.pt` が保存される。モデル本体は保存しない。既存の出力ディレクトリは
上書きせず、別の実験名を指定する。

メタデータにはモデル名、指定／解決済み revision、トークナイザーのフィンガープリント、
チャット設定、学習条件、選択データの ID と正規化した入力のハッシュ、
依存ライブラリの版、学習履歴を含む。重みファイルは SHA-256 を確認してから
`torch.load(weights_only=True)` でロードする。
同じローカルモデルパスの中身を後から書き換えたことは完全には検出できないため、
実験用モデルのディレクトリも不変のものとして管理する。

同じ入力の空白・大文字小文字・Unicode 正規化の違いによる split 重複を拒否する。
保存済みアーティファクトの選択に使った入力は、別のファイルに移しても最終評価に
使えない。意味的に同じ言い換えは自動検出しないので、必要ならタスクや概念単位で分割する。
最大長超過はエラーとし、質問や target を暗黙に切り捨てない。

`report.json` の指標は次の通り。

| フィールド | 意味 |
| --- | --- |
| `mean_target_nll` | 目的応答に対する例間平均の負の対数尤度。小さいほど高確率 |
| `exact_match_rate` | 生成全文と target の前後空白を除く完全一致率 |
| `response` | 実際の自由生成結果 |
| `input_guardrail_pass` | 外部入力ガードレール判定。未測定なので `null` |
| `output_guardrail_pass` | 外部出力ガードレール判定。未測定なので `null` |
| `behavior_success` | 意図した振る舞いを満たすかの独立判定。未測定なので `null` |

`null` を成功や失敗として集計してはいけない。特定のガードレール通過を主張するには、
そのシステムの実際の判定と生成内容の評価が別途必要。

オフラインテストは実際の小さなランダム初期化 GPT-2 とローカルトークナイザーで、
本体が変わらず追加ベクトルに勾配が流れること、validation 損失の改善、
教師応答の位置合わせ、保存・再読込、生成、データ漏洩拒否、CLI 一連の処理を確認する。

2026-09-06 の検証では、新規12件と既存設定テスト4件の計16件が
Python 3.12.11 / PyTorch 2.14.0 / Transformers 5.6.2 の CPU 環境で通過した。
新規12件は既存の Python 3.10.9 / PyTorch 2.11.0 / Transformers 4.57.1 環境でも通過した。
これは実装の動作確認であり、ランダム初期化モデルの結果を jailbreak 成功の証拠にはしない。

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_prompt_lab.py -v
```

## 研究として次に検証する仮説

以下は研究候補であって、新規性を確認済みの主張ではない。

1. 同じ固定モデルでの Soft Prompt と重み介入の違いを、タスク成功・通常能力・
   過剰拒否・計算量の同じ評価セットで比較する。
2. 入力ごと、概念ごと、全入力共通の学習ベクトルで、未知タスクへの汎化が
   どう変わるかを比較する。未知概念を独立した test に保つ。
3. テキストのみと連続ベクトルの差を測り、文字列への蒸留を追加した場合に
   残る効果を測る。文字列化できること自体を前提にしない。
4. 内部表現を目的とする方式を追加する場合、MSE-Break と比較して何が異なるかを
   先に定義する。Heretic の残差方向を使うというだけで新規性を主張しない。

現時点の実装ではこれらの比較実験や外部ガードレール連携まで完了したとは扱わない。
