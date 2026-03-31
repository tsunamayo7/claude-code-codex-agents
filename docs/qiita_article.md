---
title: "MCPで異なるAIモデルを連携させる実践ガイド — Claude Code × Codex CLI (GPT-5.4)"
tags: Python, MCP, AI, Claude, OpenAI
---

## はじめに

Claude Code（Opus 4.6）とOpenAI Codex CLI（GPT-5.4）を**MCPプロトコル**で連携させると、単一モデルでは得られない実用的なワークフローが実現できます。

本記事では、**claude-code-codex-agents**というMCPサーバーを使って、Claude CodeからGPT-5.4にタスクを委譲し、構造化された実行レポートを受け取る方法をハンズオンで解説します。

## アーキテクチャ

```
Claude Code (Opus 4.6)
    │ MCP Protocol
    ▼
claude-code-codex-agents (MCPサーバー)
    │ subprocess + stdin
    ▼
Codex CLI → OpenAI API (GPT-5.4)
    │ JSONL event stream
    ▼
構造化レポート → Claude Code に返却
```

ポイントは**Codex CLIが出力するJSONLイベントストリームを全解析**し、ツール呼び出し・ファイル操作・エラーを構造化レポートに変換する点です。生のテキストではなく、Claude Codeが判断材料として使える形式で返します。

## 前提条件

| 要件 | バージョン |
|------|-----------|
| Python | 3.12以上 |
| Node.js | 18以上（Codex CLI用） |
| uv | 最新版推奨 |
| OpenAIアカウント | Codex CLI認証用 |

## Step 1: Codex CLIのインストールと認証

```bash
npm install -g @openai/codex
codex login
```

`codex login` を実行するとブラウザが開き、OpenAIアカウントで認証します。認証が完了すると `~/.codex/` に認証情報が保存されます。

動作確認:

```bash
codex --version
```

## Step 2: claude-code-codex-agentsのインストール

```bash
git clone https://github.com/tsunamayo7/claude-code-codex-agents.git
cd claude-code-codex-agents
uv sync
```

`uv sync` で依存パッケージ（FastMCPとhttpx）が自動インストールされます。

## Step 3: MCPクライアントへの登録

### Claude Code の場合

`~/.claude/settings.json` に以下を追加:

```json
{
  "mcpServers": {
    "claude-code-codex-agents": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/claude-code-codex-agents", "python", "server.py"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

`/path/to/claude-code-codex-agents` は実際のクローン先パスに置き換えてください。

### Cursor / VS Code の場合

各エディタのMCP設定ファイル（`~/.cursor/mcp.json` 等）に同様の設定を追加します。

## Step 4: 利用可能なツール一覧

claude-code-codex-agentsは10個のMCPツールを提供します:

| ツール | 用途 | サンドボックス |
|--------|------|---------------|
| `execute` | タスクをCodexに委譲し構造化レポートを取得 | workspace-write |
| `trace_execute` | executeと同じ+全イベントタイムライン付き | workspace-write |
| `parallel_execute` | 最大6タスクを同時並列実行 | read-only |
| `review` | GPT-5.4によるコードレビュー（Adversarial Review） | read-only |
| `explain` | コード解説（brief/medium/detailed） | read-only |
| `generate` | コード生成（ファイル出力オプション付き） | workspace-write |
| `discuss` | 設計判断についてGPT-5.4の見解を取得 | read-only |
| `session_continue` | 前回スレッドを引き継いで継続 | workspace-write |
| `session_list` | セッション履歴の一覧表示 | - |
| `status` | Codex CLIの状態と認証確認 | - |

## Step 5: 実際に使ってみる

### 基本的なタスク委譲（execute）

Claude Codeのチャットで以下のように指示します:

```
claude-code-codex-agentsのexecuteツールを使って、src/auth.pyの認証ロジックを修正してください
```

返却されるレポート例:

```
[Codex gpt-5.4] Completed

⏱ Execution time: 8.3s
🧵 Thread: 019d436e-4c39-7093-b7ed-f8a26aca7938

📦 Tools used (3):
  ✅ read_file — src/auth.py
  ✅ edit_file — src/auth.py
  ✅ shell — python -m pytest tests/

📁 Files touched (1):
  • src/auth.py

━━━ Codex Response ━━━
Fixed the authentication logic. Token validation order was incorrect.
```

生テキストではなく、**何のツールを使い、どのファイルを変更し、どれくらい時間がかかったか**が一目でわかります。

### Adversarial Review（review）

Claude Codeが書いたコードをGPT-5.4にレビューさせる使い方です:

```
claude-code-codex-agentsのreviewツールで、さっき書いたコードをレビューしてください
```

```
[Codex Review] GPT-5.4 Review Result

⏱ Execution time: 15.7s

━━━ Codex Response ━━━
- [CRITICAL] `run(cmd)` calls `os.system(cmd)` directly -- command injection
  if `cmd` contains user input. Use `subprocess.run([...], shell=False)`.

- [WARNING] `divide(a, b)` raises ZeroDivisionError when b == 0.
  Add a pre-check or explicit error message.
```

異なるモデルが異なる視点でレビューするため、**単一モデルの盲点を補完**できます。

### 並列実行（parallel_execute）

複数ファイルを同時に分析する場合:

```
claude-code-codex-agentsのparallel_executeで以下の3ファイルを同時にセキュリティレビューしてください:
- src/auth.py
- src/db.py
- src/api.py
```

最大6タスクが並列で実行され、各タスクの結果が個別の構造化レポートとして返されます。

### セッション継続（session_continue）

大規模なリファクタリングでは、前回のスレッドを引き継いで作業を続けられます:

```
session_continueで前回のスレッド（019d436e-...）を引き継いで、残りのファイルも修正してください
```

threadIdが保持されるため、Codex側のコンテキストが維持されます。

## セキュリティモデル

claude-code-codex-agentsは3段階のサンドボックスモデルを採用しています:

| モード | ファイル書き込み | シェル実行 | 用途 |
|--------|-----------------|-----------|------|
| `read-only` | ブロック | ブロック | review, explain, discuss |
| `workspace-write` | CWDのみ | 許可 | execute, generate |
| `danger-full-access` | どこでも | 許可 | フルアクセス（要注意） |

さらに、ANSI/OSCエスケープシーケンスのサニタイズ（ターミナルインジェクション防止）、入力バリデーション、タイムアウト時のプロセスkillも実装されています。

## なぜMCPでモデル連携するのか

1. **盲点の補完**: 同じモデルが書いてレビューしても見落としは残る。異なるモデルの視点が有効
2. **得意分野の活用**: Claude Codeは対話的な開発に強く、GPT-5.4はコード生成・分析に強い。適材適所で使い分けられる
3. **構造化された連携**: 生テキストの受け渡しではなく、MCPプロトコルによる型付きの情報交換ができる
4. **再現性**: MCPツールとして定義されているため、同じ操作を何度でも一貫して実行できる

## まとめ

claude-code-codex-agentsを使えば、MCPプロトコルを介してClaude CodeとGPT-5.4を実用的に連携させることができます。

- **インストール**: `git clone` + `uv sync` で完結
- **設定**: MCPクライアントの設定ファイルに数行追加するだけ
- **単一ファイル構成**: server.py（約820行）のみ。読みやすく改変も容易
- **56テスト**: パース、セキュリティ、セッション管理、エッジケースをカバー

リポジトリ: https://github.com/tsunamayo7/claude-code-codex-agents
ライセンス: MIT

異なるAIモデルを連携させるMCPサーバーの実装例として、参考になれば幸いです。
