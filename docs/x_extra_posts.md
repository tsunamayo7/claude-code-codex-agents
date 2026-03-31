# X 追加投稿（日本語・多角度）

各投稿は独立してポストできる。自動翻訳で海外リーチも狙う。

---

## 投稿A: AI協調の未来（思想系）

AIが書いたコードを、同じAIがレビューする。
「自分で書いた答案を自分で採点する」のと同じで、盲点が消えない。

helix-codexは、Claude(Opus 4.6)が書いたコードをGPT-5.4がレビューする仕組み。

実際にやってみたら、GPT-5.4が「returncode判定の論理バグ」「terminal injection脆弱性」「パス二重適用」の3件のCRITICALを検出した。

Claude単体では見落としていた問題。
マルチモデル協調は「贅沢」ではなく「必要」。

https://github.com/tsunamayo7/helix-codex

---

## 投稿B: 開発ログ系

helix-codex開発で一番驚いたこと。

Codex CLIのJSONL出力のフィールド名が、ドキュメントでは「threadId」（キャメルケース）なのに、実際の出力は「thread_id」（スネークケース）だった。

テストは通るのにセッション管理が動かない。原因特定に30分。

教訓: APIの実出力は必ず生データを確認すること。ドキュメントを信じるな。

（この修正で `event.get("thread_id") or event.get("threadId")` という両対応コードが生まれた）

---

## 投稿C: 数字で語る系

helix-codex v0.2.0 の実測値:

- explain（コード解説）: 5.4秒
- review（コードレビュー）: 15.7秒
- execute（タスク実行）: 2.8秒
- parallel_execute（3タスク並列）: 全完了
- session_continue（セッション継続）: 正しく引き継ぎ

56テスト全パス。server.py 1ファイル約820行。
外部依存ゼロ（FastMCP + Codex CLIのみ）。

Claude CodeからGPT-5.4を構造化トレース付きで呼べるMCPサーバー。

https://github.com/tsunamayo7/helix-codex

---

## 投稿D: セキュリティ系

AIツール開発で見落とされがちなセキュリティ。

helix-codexでは:

1. サンドボックスポリシー（3段階）
   - read-only: ファイル変更・コマンド実行を禁止
   - workspace-write: 作業ディレクトリのみ
   - danger-full-access: 全許可（要注意）

2. Terminal injection防止
   - ANSI/OSCエスケープシーケンスをサニタイズ
   - Codexの出力にANSI制御文字が含まれていても安全

3. 入力バリデーション

GPT-5.4がレビューで「format_report()に制御文字サニタイズがない」とCRITICALを出してくれた。

AI同士のレビューでセキュリティが向上する。

---

## 投稿E: 比較系

Claude Code × Codex CLI のMCPブリッジ、GitHub上に6個以上ある。

全部試した結論:

ほとんどは「codex exec を呼んで生テキストを返すだけ」の薄いラッパー。

helix-codexだけが:
✅ JSONLイベント全解析（ツール使用・ファイル操作・エラーを構造化）
✅ 最大6タスク並列実行
✅ セッション管理（threadId継続）
✅ Adversarial Review（GPT-5.4がClaudeのコードをレビュー）
✅ 3層サンドボックス + terminal injection防止
✅ 56テスト

「AIにAIの仕事を見える化させる」のが本質。

https://github.com/tsunamayo7/helix-codex

---

## 投稿F: 実演系

Claude Code内でClaude AgentとCodex(GPT-5.4)を同時に呼び出して同じ質問をした。

質問: 「Pythonでスレッドセーフなシングルトンの最適解は？」

Claude: メタクラス+Lock / モジュール変数 / __new__ の3パターン
Codex: モジュール変数 / lru_cache / Lock+classmethod の3パターン

Codexの「lru_cache」はClaudeにはなかった視点。

別モデルに聞くと本当に別の答えが出る。
これが「AI Second Opinion」の価値。

helix-codex: https://github.com/tsunamayo7/helix-codex

---

## 投稿G: 初心者向け解説系

MCPって何？

Model Context Protocol。AIツール同士が会話するための「共通言語」。

例えば:
- Claude Code（考える担当）
- Codex CLI（コードを書く担当）

この2つを繋ぐのがMCPサーバー。

helix-codexは、Claude CodeがCodex CLIに仕事を頼んで、その「仕事報告書」を受け取るMCPサーバー。

しかも報告書が構造化されてる。
何のツール使った？どのファイル触った？何秒かかった？エラーは？

全部一目でわかる。

https://github.com/tsunamayo7/helix-codex

---

## 投稿H: 転職・ポートフォリオ系

個人開発プロジェクト helix-codex をGitHub公開しました。

概要:
- Claude Code（Opus 4.6）がCodex CLI（GPT-5.4）をMCPサーバー経由で操作
- JSONLストリーム全解析 → 構造化レポート
- 並列実行・セッション管理・Adversarial Review

技術スタック:
- Python 3.12 / FastMCP / asyncio
- 56テスト（pytest）
- セキュリティ設計（サンドボックスポリシー、terminal injection防止）

「AIエージェント間の協調」という新しい領域を実装レベルで形にしました。

https://github.com/tsunamayo7/helix-codex

#個人開発 #ポートフォリオ #AI #Python
