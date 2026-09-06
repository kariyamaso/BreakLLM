# 固定モデル安全性評価・実験監査

監査日: 2026-09-06  
総合判定: **WARN（適用範囲・監査方式に制約）**  
成果物の整合性: **PASS**

独立した Codex レビューエージェントが、実行担当の結果要約を根拠にせず、コード・原本・実行記録・公開集計を直接照合した。実行担当とは同一モデル系統であり、指定の異なるモデル系統のバックエンドが利用不可だったため、**cross-model 検証は未実施**。人手による内容評価でもない。

## A–F の判定

| 項目 | 判定 | 根拠 |
| --- | --- | --- |
| A. 正解・出典の由来 | PASS | 3原本の固定SHA-256と、独立に再構成した全1,759行が一致。応答ラベルは明示されたモデル代理評価。src/heretic/chat_service/safety_datasets.py:183、reports/safety-ja/summary.json:13。 |
| B. 分母・正規化 | PASS | 完了かつ判定可能な1,705応答が分母。打ち切り54件は未評価。自己出力の最大値等による正規化なし。src/heretic/chat_service/safety_benchmark.py:84、reports/safety-ja/summary.json:103。 |
| C. 結果の実在・一致 | PASS | completeと完了時刻、全ID・全質問/応答/判定ハッシュを照合。全体・3データ・19カテゴリの全指標と日本語表の件数・丸め値が独立再計算に一致。prompt-runs/safety-ja/full-1024/run.json:4、同:44033、reports/safety-ja/summary.json:11、reports/safety-ja/report.ja.md:14。 |
| D. 指標の実行経路 | PASS | 解析・集計関数が収集/出力経路から呼ばれ、結果が存在。未使用指標を検出せず。src/heretic/chat_service/safety_benchmark.py:176、同:469、同:506。 |
| E. 実証範囲 | WARN | 生成モデル1種・判定器1種・seed42・1設定。HarmBenchの31/300件を含む54応答が打ち切り。完了応答の率を全応答・他モデル・他言語に一般化できない。既存文書に過大な包括性主張はない。reports/safety-ja/summary.json:55、同:148。 |
| F. 評価種別 | PASS | synthetic_proxyに相当するguard_model_proxy。公式スコア・jailbreak ASR・人手評価はnull。reports/safety-ja/summary.json:5、docs/safety_benchmark.md:80。 |

## 照合結果

対象・生成は1,759件、完了1,705件、打ち切り54件。完了応答への自動判定はSafe 1,508、Unsafe 143、Controversial 54。拒否941/1,705、判定カバレッジ1,705/1,759（96.93%）。全1,759件の判定器出力は終端まで生成され、判定器エラーは0件だった。打ち切られた対象応答への判定は主要指標に含まれていない。

公開表の件数・率・拒否分母と文書の実行件数が一致した（reports/safety-ja/report.ja.md:14、同:23、docs/safety_benchmark.md:7）。日本語データ939応答すべてに仮名が含まれるという記述も一致するが、言語品質の保証ではない（docs/safety_benchmark.md:11、reports/safety-ja/summary.json:179）。

安全評価テストは**16件成功・0件失敗**。出典再照合、再ハッシュされた改変の拒否、質問/応答に対する判定の対応、打ち切り除外、分母、選択ID、全文を含まない集計を確認した（tests/test_safety_datasets.py:173、tests/test_safety_benchmark.py:64、同:96、同:163）。

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_safety*.py' -v

## 結論と限界

修正が必要な数値・出典・対応関係の不整合は検出しなかった。公開値は、既存レポートどおり**完了応答に条件付けた自動判定率**として、分母・カバレッジ・打ち切り数と併記する。打ち切りが無作為とは確認しておらず、未評価54件を含む全応答の安全率とは扱わない。

人手評価、判定器の誤分類率・校正、正確性・有用性・過剰拒否は未測定。GPU推論の独立再実行やモデル重み全体の独立照合も未実施。独立した実験トラッカーは提示されていないため、完了はrun.jsonのstatusとcompleted_atで確認した。

ファイルSHA-256と各判定の詳細は[EXPERIMENT_AUDIT.json](EXPERIMENT_AUDIT.json)に保存した。summary.jsonのrun_sha256は整列したJSONオブジェクトのハッシュであり、run.jsonファイル全体のハッシュとは区別して記録した。質問・応答本文は監査成果物に含めていない。
