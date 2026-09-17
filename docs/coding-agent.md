# hb-gpu-0 の Qwen coding agent

指定された [DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF](https://huggingface.co/DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF)
を llama.cpp で動かし、OpenCode から利用する構成です。
OpenCode がコードの検索・読み取り・編集・コマンド実行と、その結果に応じた続行を担当します。

## 各自のPCから使う（推奨）

`ssh hb-gpu-0` で接続できる人は、自分のPC（macOS / Linux / WSL）のどのディレクトリからでも使えます。
OpenCodeは手元で動くので、手元のファイルを編集し、手元でコマンドを実行します。
推論だけをSSHトンネル経由でhb-gpu-0のモデルに送ります。会話履歴も各自のPCに保存されます。

```bash
# インストール（python3・curl・tarが必要。OpenCodeはSHA-256を確認して取得します）
ssh -o ClearAllForwardings=yes hb-gpu-0 bash /home/ubuntu/kariyama/BreakLLM/deploy/coding-agent/local/bundle.sh | bash
# SSHのホスト名やローカルのポートが違う場合
ssh -o ClearAllForwardings=yes myhost bash /home/ubuntu/kariyama/BreakLLM/deploy/coding-agent/local/bundle.sh | bash -s -- --host myhost --port 18787

cd ~/any/project
qwen-code --new          # 新規会話
qwen-code                # このディレクトリの直前の会話を再開
qwen-code run '依頼内容'  # 1回だけ実行
qwen-code history        # 会話の一覧
qwen-code tunnel status  # トンネルの状態 / tunnel stop で切断
qwen-code update         # サーバーの最新版で入れ直す
```

インストール先は `~/.local/share/qwen-code/`（本体・設定）と `~/.local/bin/qwen-code` です。
トンネルは初回実行時に1本だけ張られ、同じユーザーの複数ウィンドウで共有します。
`~/.ssh/config` の `LocalForward` は起動しません。`127.0.0.1:18787` が使用中の場合は
`QWEN_CODE_PORT_OVERRIDE=<空きポート> qwen-code` で変更できます。
PCがスリープするとトンネルは切れます。`qwen-code` を再実行すると張り直して会話を再開します。
`app` コマンドはサーバー上でのみ使えます。
`~/.claude/CLAUDE.md` やスキルは読み込みません（`OPENCODE_DISABLE_CLAUDE_CODE=1`）。
読み込むと、2行のファイルの修正でも最初の入力が21,282トークンになり、毎ステップ圧縮が走って終わりませんでした。
無効化後は7,904トークンで、修正とテスト実行まで44秒で完了しました（2026-09-17の実測）。

モデルの推論スロットは1本です。何人でも接続できますが、生成は1件ずつ順番に処理されます。
別の人の会話に切り替わるたびに、長い入力（1〜2万トークン）の読み直しに6〜13秒かかります（2026-09-17の実測）。

## hb-gpu-0上で使う

このリポジトリを開いたローカル端末から:

```bash
bash deploy/coding-agent/connect.sh
```

2026-09-07の修正後は、同じ作業ディレクトリの直近の会話を自動的に再開します。
起動中のOpenCodeには古い設定が残るため、一度終了して上のコマンドで再接続してください。

```bash
# 保存済みの会話を一覧表示（推論モデルが停止していても利用可能）
bash deploy/coding-agent/connect.sh history

# 新規会話 / 指定した会話を再開
bash deploy/coding-agent/connect.sh --new
bash deploy/coding-agent/connect.sh --session ses_...

# 作業先・選択される履歴を起動せず確認
bash deploy/coding-agent/connect.sh --dry-run

# 開発UIを起動し、HTTPとAPIの疎通を確認
bash deploy/coding-agent/connect.sh app start
bash deploy/coding-agent/connect.sh app status
bash deploy/coding-agent/connect.sh app logs
bash deploy/coding-agent/connect.sh app stop
```

開発UIは `http://100.91.77.114:8771/` です。開発用cloneのソースを読み込み、
既存の `http://100.91.77.114:8768/` のAPIを共有します。GPUモデルは追加しません。
`8768` は稼働中の配布版、`8770` は配布版への転送です。
起動した開発UIはSSHやOpenCodeを終了しても継続します。ホスト再起動後は `app start` を実行します。
稼働済み・ポート競合・起動失敗では成功URLを返さず、原因と確認方法を表示します。

別のHTTPアプリもプロジェクト単位で管理できます。以下の例は hb-gpu-0 上で実行します。

```bash
qwen-code app start demo --port 8772 -- python3 -m http.server 8772 --bind 100.91.77.114
qwen-code app status demo
qwen-code app logs demo
qwen-code app stop demo
```

履歴のDBは `~/.local/share/opencode/opencode.db` です（`XDG_DATA_HOME` 指定時はその配下）。
自動再開では別ディレクトリ・サブエージェント・アーカイブ済みの会話を選びません。
長い履歴は `/compact` でも圧縮できます。自動圧縮は16,384トークンから開始する設定に変更しました。
各ツールの出力は先頭6,000バイトに制限し、全文は `~/.local/share/opencode/qwen-tool-output/` に保存します。
全文の保存先を応答に示すので、必要な行だけ読み返せます。元の会話DBを削除・初期化する処理はありません。

既定の開発用cloneは `/home/ubuntu/kariyama/BreakLLM-dev` です。
引数にディレクトリを渡すと、hb-gpu-0 上の別プロジェクトで作業できます。
ファイル編集とコマンド実行は hb-gpu-0 上で行われます。

すでに hb-gpu-0 にログインしている場合:

```bash
cd /home/ubuntu/kariyama/BreakLLM-dev
/home/ubuntu/.local/bin/qwen-code

# 1回の依頼をコマンドから実行
/home/ubuntu/.local/bin/qwen-code run 'コードを調べて、不具合を修正し、関連テストを実行してください'
```

対話画面では通常の Build モードで編集・実行でき、Tab で Plan モードに切り替えられます。
モデルと補助処理のモデルはともに `hb-gpu-0/qwen3.8-27b-turbo` に固定しています。
既存の Claude Code / Codex の設定とは別の起動コマンドです。

## 構成

| 項目 | 設定 |
| --- | --- |
| GPU | NVIDIA RTX PRO 6000 Blackwell Workstation Edition、96GB |
| モデルファイル | `Qwen3.8-27B-TurboFCFusion-735-882-Here-Uncen-NEO-CODER-MAX-Q5_K_M.gguf` |
| 量子化・容量 | Q5_K_M、20,730,952,288 bytes |
| コンテキスト | 入出力合計32,768トークン、生成上限8,192 |
| 同時推論 | 1スロット |
| KVキャッシュ | K/VともQ8_0、Flash Attention有効 |
| サンプリング | temperature 0.6、top_p 0.95、top_k 20 |
| reasoning | low、モデル付属Jinjaテンプレート |
| OpenCode | 1.18.29 |
| API | `http://127.0.0.1:8787/v1`、OpenAI互換 |
| モデル保存先 | `/home/ubuntu/kariyama/BreakLLM/.coding-agent/models/` |
| systemd | `breakllm-coding-model.service`、ubuntuのユーザーサービス |

このリポジトリには通常版とMTP版の量子化ファイルがあります。現在の設定は通常版Q5_K_Mを使用し、MTP推測デコードは有効にしていません。
モデル名から想定される性能と実際のコーディング能力は別であり、大規模なコーディングベンチマークは実施していません。

モデルrevision・SHA-256・推論エンジンのcommit・OpenCode配布物のSHA-256は
[`versions.env`](../deploy/coding-agent/versions.env) に固定しています。

## 実動作の確認

2026-09-06、hb-gpu-0で以下を確認しました。

- OpenAI互換APIのストリーミングツール呼び出しと、ツール実行結果の利用: 合格。
- OpenCodeの実際の `glob` / `read` / `edit` / `bash` 実行: 合格。
- 平均値関数の修正課題: 5テストを変更せずに全件合格、約16秒。
- モデルプロセスのGPUメモリ使用量: 20,970MiB。

[実測JSON](../reports/coding-agent-verification.json) にツール呼び出しと修正内容を保存しています。

## 開発用clone

開発元は `https://github.com/kariyamaso/BreakLLM.git`、ブランチは `master` です。
開発用のcloneと、推論サービスの配置先 `/home/ubuntu/kariyama/BreakLLM` は独立しています。
agentの推論には稼働中のサーバーを使うので、clone側にGGUFを複製する必要はありません。

開発用clone内のPython依存関係は `uv.lock` から、フロントエンドは `package-lock.json` から用意します。

```bash
cd /home/ubuntu/kariyama/BreakLLM-dev
uv sync --frozen --extra chat
npm --prefix chat-ui ci

PYTHONPATH=src:tests uv run --frozen --extra chat python -m unittest \
  test_prompt_lab test_chat_service test_refusal_audit test_safety_benchmark test_safety_datasets
npm --prefix chat-ui run build
/home/ubuntu/.local/bin/qwen-code
```

## 運用

以下は hb-gpu-0 上で実行します。

```bash
systemctl --user status breakllm-coding-model.service --no-pager
journalctl --user -u breakllm-coding-model.service -n 60 --no-pager
curl --fail http://127.0.0.1:8787/health
curl --fail http://127.0.0.1:8787/v1/models

# GPUメモリを解放する
systemctl --user stop breakllm-coding-model.service

# 再び利用する
systemctl --user start breakllm-coding-model.service

# ホスト起動時の自動起動を解除する
systemctl --user disable breakllm-coding-model.service
```

APIはサーバー自身のループバックにバインドしています。
ローカルPCで別のOpenAI互換クライアントを使う場合は、トンネルを開きます。

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:18787:127.0.0.1:8787 hb-gpu-0
```

クライアントのURLを `http://127.0.0.1:18787/v1`、モデル名を `qwen3.8-27b-turbo` に設定します。
SSH設定にほかのLocalForwardがある場合は、その転送も起動されます。
通常のリモート作業には、転送の競合を避ける `connect.sh` を利用してください。

## 再構築と検証

以下も hb-gpu-0 上で実行します。初回導入にはモデル取得とCUDAビルドが必要です。

```bash
cd /home/ubuntu/kariyama/BreakLLM
bash deploy/coding-agent/bootstrap.sh
bash deploy/coding-agent/activate.sh

# APIのストリーミングツール往復と、隔離した小規模プロジェクトの編集・テスト
python3 deploy/coding-agent/verify.py --output .coding-agent/verification-new
```

検証用出力先には未使用のディレクトリを指定します。
API検証では、モデルがツールを呼び、その結果で初めて渡されるランダムなコードを返せるかを確認します。
エージェント検証では、実装の不具合を修正し、既存の5テストを変更せずに通す課題を使います。
`report.json`、`opencode-events.jsonl`、`unittest.log` に実際の結果が残ります。

参考: [OpenCodeのllama.cpp設定](https://opencode.ai/docs/providers/#llamacpp)、
[llama.cppのfunction calling](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)。
