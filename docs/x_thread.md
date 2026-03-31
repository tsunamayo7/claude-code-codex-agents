# X (Twitter) スレッド — claude-code-codex-agents

---

## Tweet 1（フック）

Claude CodeからGPT-5.4を呼び出したい。

でもCodex CLIの出力って「生テキストの壁」なんですよね。何のツール使ったか、どのファイル変えたか、成功したかも分からない。

この問題、MCPサーバー1本で解決しました。

#ClaudeCode #GPT5 #AI開発

---

## Tweet 2（解決策）

claude-code-codex-agents を作りました。

Codex CLIのJSONLイベントストリームを**全パース**して、構造化レポートに変換するMCPサーバーです。

- 使用ツール一覧
- 変更ファイル一覧
- 実行時間
- エラー詳細

Claude Codeが「GPT-5.4の仕事内容」を正確に把握できるようになります。

---

## Tweet 3（差別化）

GitHubにCodex MCPブリッジは6個以上ある。でも全部「生テキスト返すだけ」。

claude-code-codex-agentsだけが違う点:
- 構造化トレース（ツール・ファイル・タイミング）
- 最大6タスク並列実行
- セッション継続（threadId永続化）
- 3層サンドボックス+ターミナルインジェクション防止
- 56テスト

---

## Tweet 4（実動作: review出力例）

実際のreview出力がこれ:

```
[Codex Review] GPT-5.4 Review Result
⏱ 15.7s

- [CRITICAL] run(cmd)がos.system直呼び
  → コマンドインジェクション脆弱性
- [WARNING] divide(a,b)のゼロ除算未処理
- [INFO] 型ヒントなし
```

ツール使用・実行時間・重要度分類まで構造化されて返ってくる。

---

## Tweet 5（Adversarial Review）

一番面白い機能が「Adversarial Review」。

Claude(Opus 4.6)がコードを書く
→ GPT-5.4がそのコードをレビューする

同じモデルが書いて同じモデルがレビューすると見落とすバグを、別モデルの視点で発見できる。

「AI Second Opinion」という概念、もっと広まるべき。

---

## Tweet 6（並列実行+セッション）

地味に便利な機能:

parallel_execute: 最大6ファイルを同時分析。セキュリティ監査が一発で終わる。

session_continue: threadIdで前回の続きから。大規模リファクタリングをCodexに委譲しながら、文脈を維持できる。

依存ゼロ。FastMCP + Codex CLIだけ。DB不要、Docker不要。

---

## Tweet 7（CTA）

claude-code-codex-agents — Claude CodeにGPT-5.4の構造化トレースを。

- Python 3.12+
- server.py 1ファイル（約820行）
- MITライセンス
- 56テスト通過

Starしてくれると励みになります。

https://github.com/tsunamayo7/claude-code-codex-agents

---

## Tweet 8（英語ハイライト）

For English speakers:

claude-code-codex-agents gives Claude Code **structured Codex traces**, not raw text.

- Full JSONL event parsing (tools, files, timing, errors)
- Up to 6 parallel tasks
- Adversarial Review: GPT-5.4 reviews Claude's code
- 56 tests, zero external deps

https://github.com/tsunamayo7/claude-code-codex-agents

#AI #MCP #ClaudeCode #OpenAI
