"""
claude-code-codex-agents: Claude CodeがCodex CLI(GPT-5.4)を手足として使いこなすためのMCPサーバー。

アーキテクチャ:
  Claude Code (司令塔) → claude-code-codex-agents MCP → Codex CLI → OpenAI API (GPT-5.4)
                                ↓
                      JSONLストリーム解析 → 構造化レポート

Codexの思考・ツール使用・ファイル操作をリアルタイム解析し、
Claude Code側に可視化レポートとして返す。外部ツール依存なし。

機能:
  - execute: タスク委譲+実行過程の構造化レポート
  - review: コードレビュー（Adversarial Review Loop）
  - explain: コード解説・分析
  - generate: コード生成
  - discuss: 対話的議論（別視点）
  - trace_execute: 全JSONLイベントを解析し実行トレースを返す
  - parallel_execute: 複数タスクをサブプロセスで並列実行
  - session_continue: 前回のスレッドを引き継いで継続実行
  - session_list: セッション履歴の一覧
"""

import asyncio
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

mcp = FastMCP("claude-code-codex-agents")

# デフォルト設定
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_SANDBOX = "read-only"
DEFAULT_TIMEOUT = 120  # 秒

VALID_SANDBOXES = frozenset({"read-only", "workspace-write", "danger-full-access"})

# ANSI/OSC制御文字のパターン（terminal injection防止）
_CONTROL_RE = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]"   # CSI sequences: ESC[...m, ESC[...H, etc.
    r"|\x1b\][^\x07]*\x07"     # OSC sequences: ESC]...BEL
    r"|\x1b[^[\]()]"           # Other ESC sequences
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # C0 control chars (except \t \n \r)
)


def _sanitize(text: str) -> str:
    """制御文字・ANSIエスケープシーケンスを除去"""
    return _CONTROL_RE.sub("", text)

# セキュリティ: sandboxモードごとに許可されるツールカテゴリ
SANDBOX_POLICIES = {
    "read-only": {
        "description": "読み取り専用。ファイル変更・コマンド実行を禁止",
        "blocked_tools": {"execute", "generate"},
        "allowed_codex_sandbox": "read-only",
    },
    "workspace-write": {
        "description": "作業ディレクトリ内のみ書き込み可",
        "blocked_tools": set(),
        "allowed_codex_sandbox": "workspace-write",
    },
    "danger-full-access": {
        "description": "全アクセス（要注意）",
        "blocked_tools": set(),
        "allowed_codex_sandbox": "danger-full-access",
    },
}


# =============================================================================
# セッション管理
# =============================================================================

@dataclass
class SessionRecord:
    """Codex実行セッションの記録"""
    thread_id: str
    model: str
    prompt: str
    started_at: float
    ended_at: float = 0.0
    success: bool = False
    tool_count: int = 0
    files_touched: list[str] = field(default_factory=list)
    summary: str = ""


class SessionManager:
    """Codexセッション（threadId）の管理。継続実行を可能にする。"""

    def __init__(self, max_sessions: int = 20):
        self._sessions: list[SessionRecord] = []
        self._max = max_sessions

    def record(self, trace: "CodexTrace", prompt: str) -> None:
        """トレースからセッションを記録"""
        if not trace.thread_id:
            return
        rec = SessionRecord(
            thread_id=trace.thread_id,
            model=trace.model,
            prompt=prompt[:200],
            started_at=trace.started_at,
            ended_at=trace.ended_at,
            success=not bool(trace.errors),
            tool_count=len(trace.tool_calls),
            files_touched=list(dict.fromkeys(trace.files_touched)),
            summary=trace.messages[0][:200] if trace.messages else "",
        )
        self._sessions.append(rec)
        # 古いセッションを削除
        if len(self._sessions) > self._max:
            self._sessions = self._sessions[-self._max:]

    def get_latest(self) -> Optional[SessionRecord]:
        """最新のセッションを取得"""
        return self._sessions[-1] if self._sessions else None

    def get_by_thread(self, thread_id: str) -> Optional[SessionRecord]:
        """スレッドIDでセッションを検索"""
        for s in reversed(self._sessions):
            if s.thread_id == thread_id:
                return s
        return None

    def list_all(self) -> list[SessionRecord]:
        """全セッション一覧"""
        return list(reversed(self._sessions))

    def format_list(self) -> str:
        """セッション一覧の文字列表現"""
        if not self._sessions:
            return "(セッション履歴なし)"
        lines = []
        for i, s in enumerate(reversed(self._sessions)):
            age = time.time() - s.started_at
            if age < 60:
                age_str = f"{age:.0f}秒前"
            elif age < 3600:
                age_str = f"{age / 60:.0f}分前"
            else:
                age_str = f"{age / 3600:.1f}時間前"
            status = "✅" if s.success else "❌"
            lines.append(
                f"  {i+1}. {status} [{age_str}] {s.model} "
                f"| tools:{s.tool_count} files:{len(s.files_touched)} "
                f"| {s.prompt[:60]}..."
            )
            lines.append(f"     thread: {s.thread_id}")
        return "\n".join(lines)


# シングルトン
sessions = SessionManager()


AGENT_ROLE_PROMPTS = {
    "default": (
        "You are a Claude Code-style Codex sub-agent. "
        "Complete the assigned software task fully, keep the work pragmatic, "
        "and return concise high-signal results."
    ),
    "explorer": (
        "You are a read-heavy Claude Code-style explorer agent. "
        "Focus on investigation, concrete findings, and file references. "
        "Avoid file edits unless the caller explicitly asks for changes."
    ),
    "worker": (
        "You are an implementation-focused Claude Code-style worker agent. "
        "Make targeted changes, run relevant checks, and report what changed, "
        "what was verified, and any residual risk."
    ),
}


def _default_agent_sandbox(agent_type: str) -> str:
    return "read-only" if agent_type == "explorer" else "workspace-write"


def _normalize_agent_type(agent_type: str) -> str:
    return agent_type if agent_type in AGENT_ROLE_PROMPTS else "default"


def _summarize_agent_report(report: str) -> str:
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    if not lines:
        return ""
    summary_lines = lines[:6]
    return _sanitize("\n".join(summary_lines))[:1200]


@dataclass
class CodexAgentTurn:
    prompt: str
    success: bool
    summary: str
    report: str
    thread_id: Optional[str]
    finished_at: float


@dataclass
class CodexAgentRecord:
    agent_id: str
    description: str
    agent_type: str
    model: str
    sandbox: str
    cwd: Optional[str]
    created_at: float
    updated_at: float
    status: str = "idle"
    last_prompt: str = ""
    last_summary: str = ""
    last_report: str = ""
    last_thread_id: Optional[str] = None
    last_success: Optional[bool] = None
    history: list[CodexAgentTurn] = field(default_factory=list)
    current_task: Optional[asyncio.Task] = field(default=None, repr=False)
    closed: bool = False


class CodexAgentManager:
    def __init__(self, max_agents: int = 16):
        self._agents: dict[str, CodexAgentRecord] = {}
        self._order: list[str] = []
        self._max_agents = max_agents

    def create(
        self,
        description: str,
        agent_type: str,
        model: str,
        sandbox: str,
        cwd: Optional[str],
    ) -> CodexAgentRecord:
        normalized_type = _normalize_agent_type(agent_type)
        resolved_sandbox = sandbox or _default_agent_sandbox(normalized_type)
        now = time.time()
        agent = CodexAgentRecord(
            agent_id=f"codex-{uuid.uuid4().hex[:8]}",
            description=description.strip(),
            agent_type=normalized_type,
            model=model,
            sandbox=resolved_sandbox,
            cwd=cwd,
            created_at=now,
            updated_at=now,
        )
        self._agents[agent.agent_id] = agent
        self._order.append(agent.agent_id)
        self._trim_idle_agents()
        return agent

    def _trim_idle_agents(self) -> None:
        if len(self._order) <= self._max_agents:
            return
        removable: list[str] = []
        for agent_id in self._order:
            agent = self._agents.get(agent_id)
            if not agent:
                removable.append(agent_id)
                continue
            if agent.current_task is None and (agent.closed or agent.status in {"completed", "failed"}):
                removable.append(agent_id)
            if len(self._order) - len(removable) <= self._max_agents:
                break
        for agent_id in removable:
            self._agents.pop(agent_id, None)
            if agent_id in self._order:
                self._order.remove(agent_id)

    def get(self, agent_id: str) -> Optional[CodexAgentRecord]:
        return self._agents.get(agent_id)

    def list_all(self) -> list[CodexAgentRecord]:
        return [self._agents[agent_id] for agent_id in reversed(self._order) if agent_id in self._agents]

    def _build_prompt(self, agent: CodexAgentRecord, prompt: str) -> str:
        sections = [AGENT_ROLE_PROMPTS[agent.agent_type]]
        if agent.description:
            sections.append(f"Agent description:\n{agent.description}")
        if agent.history:
            history_lines = []
            for turn in agent.history[-3:]:
                history_lines.append(f"- Previous instruction: {turn.prompt[:400]}")
                if turn.thread_id:
                    history_lines.append(f"  Thread: {turn.thread_id}")
                if turn.summary:
                    history_lines.append(f"  Result summary:\n{turn.summary[:1000]}")
            sections.append("Prior agent context:\n" + "\n".join(history_lines))
        sections.append(f"Current assignment:\n{prompt.strip()}")
        return "\n\n".join(sections)

    async def _run_turn(
        self,
        agent: CodexAgentRecord,
        prompt: str,
        timeout: int,
    ) -> None:
        agent.status = "running"
        agent.updated_at = time.time()
        agent.last_prompt = prompt
        try:
            result = await run_codex(
                prompt=self._build_prompt(agent, prompt),
                model=agent.model,
                sandbox=agent.sandbox,
                cwd=agent.cwd,
                timeout=timeout,
            )
            report = result.get("content", "")
            summary = _summarize_agent_report(report)
            agent.last_summary = summary
            agent.last_report = report
            agent.last_thread_id = result.get("thread_id")
            agent.last_success = bool(result.get("success"))
            agent.history.append(
                CodexAgentTurn(
                    prompt=prompt,
                    success=bool(result.get("success")),
                    summary=summary,
                    report=report,
                    thread_id=result.get("thread_id"),
                    finished_at=time.time(),
                )
            )
            agent.status = "completed" if result.get("success") else "failed"
        except asyncio.CancelledError:
            agent.status = "closed"
            agent.last_success = False
            agent.last_summary = "Agent run was cancelled before completion."
            agent.last_report = agent.last_summary
            raise
        finally:
            agent.updated_at = time.time()
            agent.current_task = None

    def start(self, agent: CodexAgentRecord, prompt: str, timeout: int) -> CodexAgentRecord:
        if agent.closed:
            raise ValueError("Agent is already closed.")
        if agent.current_task is not None:
            raise ValueError("Agent is already running.")
        agent.current_task = asyncio.create_task(self._run_turn(agent, prompt, timeout))
        return agent

    async def wait(self, agent: CodexAgentRecord, timeout: int) -> dict:
        if agent.current_task is None:
            return self.snapshot(agent)
        try:
            await asyncio.wait_for(asyncio.shield(agent.current_task), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.snapshot(agent)

    def close(self, agent: CodexAgentRecord) -> CodexAgentRecord:
        if agent.current_task is not None:
            raise ValueError("Agent is still running. Wait for completion before closing it.")
        agent.closed = True
        agent.status = "closed"
        agent.updated_at = time.time()
        return agent

    def snapshot(self, agent: CodexAgentRecord) -> dict:
        return {
            "ok": True,
            "agent_id": agent.agent_id,
            "description": agent.description,
            "agent_type": agent.agent_type,
            "model": agent.model,
            "sandbox": agent.sandbox,
            "cwd": agent.cwd,
            "status": agent.status,
            "closed": agent.closed,
            "history_count": len(agent.history),
            "last_prompt": agent.last_prompt,
            "last_summary": agent.last_summary,
            "last_thread_id": agent.last_thread_id,
            "last_success": agent.last_success,
            "last_report": agent.last_report,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }


codex_agents = CodexAgentManager()


# =============================================================================
# JSONLイベント解析エンジン
# =============================================================================

@dataclass
class CodexEvent:
    """Codex CLIのJSONLイベント"""
    event_type: str
    timestamp: float
    data: dict = field(default_factory=dict)


@dataclass
class CodexTrace:
    """Codex実行のトレース（全イベントの構造化記録）"""
    thread_id: Optional[str] = None
    model: str = ""
    events: list[CodexEvent] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def elapsed(self) -> float:
        if self.started_at and self.ended_at:
            return self.ended_at - self.started_at
        return 0.0

    def add_event(self, event_type: str, data: dict):
        self.events.append(CodexEvent(
            event_type=event_type,
            timestamp=time.time(),
            data=data,
        ))

    def format_report(self, verbose: bool = False) -> str:
        """構造化レポートを生成（全出力を制御文字サニタイズ）"""
        lines = []

        # ヘッダ
        lines.append(f"⏱ 実行時間: {self.elapsed:.1f}秒")
        if self.thread_id:
            lines.append(f"🧵 Thread: {self.thread_id}")

        # ツール使用
        if self.tool_calls:
            lines.append(f"\n📦 ツール使用 ({len(self.tool_calls)}回):")
            for tc in self.tool_calls:
                name = _sanitize(str(tc.get("name", "unknown")))
                tc_status = tc.get("status", "")
                detail = _sanitize(str(tc.get("detail", "")))
                icon = "✅" if tc_status == "completed" else "⏳"
                line = f"  {icon} {name}"
                if detail:
                    line += f" — {detail[:80]}"
                lines.append(line)

        # ファイル操作
        if self.files_touched:
            unique_files = list(dict.fromkeys(self.files_touched))
            lines.append(f"\n📁 ファイル操作 ({len(unique_files)}件):")
            for f in unique_files:
                lines.append(f"  • {_sanitize(f)}")

        # エラー
        if self.errors:
            lines.append(f"\n⚠️ エラー ({len(self.errors)}件):")
            for e in self.errors:
                lines.append(f"  • {_sanitize(str(e))[:100]}")

        # メッセージ（最終応答）
        if self.messages:
            lines.append("\n━━━ Codex応答 ━━━")
            lines.append(_sanitize("\n".join(self.messages)))

        # 詳細イベントログ（verboseモード）
        if verbose and self.events:
            lines.append(f"\n━━━ イベントログ ({len(self.events)}件) ━━━")
            for ev in self.events:
                elapsed = ev.timestamp - self.started_at if self.started_at else 0
                lines.append(f"  [{elapsed:6.1f}s] {ev.event_type}")

        return "\n".join(lines)


def parse_jsonl_events(output: str, trace: CodexTrace) -> None:
    """Codex CLIのJSONL出力を解析してトレースに記録"""
    parse_errors = 0
    for line in output.strip().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue

        # dictでないJSON値はスキップ
        if not isinstance(event, dict):
            parse_errors += 1
            continue

        event_type = event.get("type", "")
        trace.add_event(event_type, event)

        # スレッド開始
        if event_type == "thread.started":
            trace.thread_id = event.get("thread_id") or event.get("threadId")

        # アイテム完了（メッセージ/ツール）
        elif event_type == "item.completed":
            item = event.get("item", {})
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")

            # エージェントメッセージ
            if item_type == "agent_message" and item.get("text"):
                trace.messages.append(str(item["text"]))

            # 通常メッセージ
            elif item_type == "message":
                for part in item.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                        trace.messages.append(str(part["text"]))

            # ツール呼び出し完了
            elif item_type == "function_call":
                tc = {
                    "name": str(item.get("name", "unknown")),
                    "status": str(item.get("status", "completed")),
                    "detail": "",
                }
                # ファイル操作を追跡
                args = item.get("arguments", "")
                if isinstance(args, str):
                    try:
                        args_dict = json.loads(args)
                        if isinstance(args_dict, dict):
                            path = args_dict.get("path") or args_dict.get("file_path", "")
                            if path:
                                tc["detail"] = str(path)
                                trace.files_touched.append(str(path))
                            cmd = args_dict.get("command", "")
                            if cmd:
                                tc["detail"] = str(cmd)[:80]
                    except (json.JSONDecodeError, AttributeError):
                        pass
                trace.tool_calls.append(tc)

        # ツール呼び出し開始（進行中の追跡）
        elif event_type == "item.created":
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") == "function_call":
                trace.add_event("tool_start", {"name": item.get("name", "")})

        # ターン完了
        elif event_type == "turn.completed":
            summary = event.get("summary", "")
            if summary and not trace.messages:
                trace.messages.append(str(summary))

        # エラー
        elif event_type == "error":
            trace.errors.append(str(event.get("message", event)))

    # 不正行があればトレースに記録
    if parse_errors:
        trace.errors.append(f"JSONL解析: {parse_errors}行スキップ")


# =============================================================================
# コアエンジン
# =============================================================================

def _validate(prompt: str, sandbox: str, model: str) -> Optional[dict]:
    """入力バリデーション。問題があればエラーdictを返す"""
    if sandbox not in VALID_SANDBOXES:
        return {
            "success": False,
            "content": f"無効なsandboxモード: '{sandbox}'。有効値: {', '.join(sorted(VALID_SANDBOXES))}",
            "thread_id": None, "model": model, "trace": None,
        }
    if not prompt or not prompt.strip():
        return {
            "success": False,
            "content": "プロンプトが空です。",
            "thread_id": None, "model": model, "trace": None,
        }
    return None


def _find_codex() -> Optional[str]:
    """Codex CLIのパスを解決"""
    return shutil.which("codex")


def _build_cmd(
    codex_path: str, model: str, sandbox: str,
) -> list[str]:
    """codex exec コマンドを構築（cwdはsubprocess側で指定、-Cとの二重適用を防ぐ）"""
    return [
        codex_path, "exec",
        "--json",
        "--model", model,
        "--sandbox", sandbox,
        "--full-auto",
        "--skip-git-repo-check",
        "--ephemeral",
        "-",  # stdin入力
    ]


def _enforce_sandbox(tool_name: str, sandbox: str) -> Optional[str]:
    """セキュリティポリシーに基づきツール使用を制限。違反時はエラーメッセージを返す"""
    policy = SANDBOX_POLICIES.get(sandbox)
    if not policy:
        return None
    if tool_name in policy["blocked_tools"]:
        return (
            f"[セキュリティ] '{tool_name}' は sandbox='{sandbox}' "
            f"({policy['description']}) では使用できません。"
            f"sandbox='workspace-write' 以上を指定してください。"
        )
    return None


async def run_codex(
    prompt: str,
    model: str = DEFAULT_MODEL,
    sandbox: str = DEFAULT_SANDBOX,
    cwd: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> dict:
    """Codex CLIを実行し、JSONLストリームを解析して構造化結果を返す"""
    # バリデーション
    err = _validate(prompt, sandbox, model)
    if err:
        return err

    codex_path = _find_codex()
    if not codex_path:
        return {
            "success": False,
            "content": "Codex CLIが見つかりません。PATHを確認してください。",
            "thread_id": None, "model": model, "trace": None,
        }

    cmd = _build_cmd(codex_path, model, sandbox)
    trace = CodexTrace(model=model, started_at=time.time())

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")), timeout=timeout
        )
        trace.ended_at = time.time()

        output = stdout.decode("utf-8", errors="replace")

        # JSONLイベントを解析
        parse_jsonl_events(output, trace)

        # stderr/returncodeチェック — 非0終了は常にfailure（部分出力があっても）
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            trace.errors.append(f"exit code {proc.returncode}: {stderr_text or '(no stderr)'}")
            sessions.record(trace, prompt)
            return {
                "success": False,
                "content": trace.format_report(verbose),
                "thread_id": trace.thread_id,
                "model": model,
                "trace": trace,
            }

        # セッション記録
        sessions.record(trace, prompt)

        return {
            "success": True,
            "content": trace.format_report(verbose),
            "thread_id": trace.thread_id,
            "model": model,
            "trace": trace,
        }

    except asyncio.TimeoutError:
        trace.ended_at = time.time()
        trace.errors.append(f"タイムアウト ({timeout}秒)")
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        sessions.record(trace, prompt)
        return {
            "success": False,
            "content": trace.format_report(verbose),
            "thread_id": None, "model": model, "trace": trace,
        }
    except Exception as e:
        trace.ended_at = time.time()
        trace.errors.append(str(e))
        sessions.record(trace, prompt)
        return {
            "success": False,
            "content": f"Codex実行エラー: {e}",
            "thread_id": None, "model": model, "trace": trace,
        }


# =============================================================================
# MCPツール
# =============================================================================

@mcp.tool()
async def execute(
    prompt: str,
    cwd: str = "",
    model: str = DEFAULT_MODEL,
    sandbox: str = "workspace-write",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Codex CLI(GPT-5.4)にタスクを委譲。実行過程を構造化レポートで返す。

    Args:
        prompt: 実行するタスクの説明（日本語OK）
        cwd: 作業ディレクトリ（空の場合はカレント）
        model: 使用モデル（デフォルト: gpt-5.4）
        sandbox: サンドボックスモード（read-only/workspace-write/danger-full-access）
        timeout: タイムアウト秒数
    """
    blocked = _enforce_sandbox("execute", sandbox)
    if blocked:
        return blocked

    result = await run_codex(
        prompt=prompt, model=model, sandbox=sandbox,
        cwd=cwd or None, timeout=timeout,
    )
    label = "実行完了" if result["success"] else "エラー"
    return f"[Codex {result['model']}] {label}\n\n{result['content']}"


@mcp.tool()
async def trace_execute(
    prompt: str,
    cwd: str = "",
    model: str = DEFAULT_MODEL,
    sandbox: str = "workspace-write",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Codex実行の全イベントトレースを返す。デバッグ・分析用の詳細モード。

    executeと同じ実行だが、全JSONLイベントのタイムラインも含む。

    Args:
        prompt: 実行するタスクの説明
        cwd: 作業ディレクトリ
        model: 使用モデル
        sandbox: サンドボックスモード
        timeout: タイムアウト秒数
    """
    blocked = _enforce_sandbox("trace_execute", sandbox)
    if blocked:
        return blocked

    result = await run_codex(
        prompt=prompt, model=model, sandbox=sandbox,
        cwd=cwd or None, timeout=timeout, verbose=True,
    )
    label = "実行完了" if result["success"] else "エラー"
    return f"[Codex Trace] {label}\n\n{result['content']}"


@mcp.tool()
async def parallel_execute(
    tasks: str,
    model: str = DEFAULT_MODEL,
    sandbox: str = "read-only",
    cwd: str = "",
    timeout: int = 180,
) -> str:
    """複数タスクをサブプロセスで並列実行し、全結果をまとめて返す。

    各タスクの実行過程が個別にトレースされ、構造化レポートで返る。

    Args:
        tasks: タスクリスト（改行区切り。各行が1つのタスク）
        model: 使用モデル
        sandbox: サンドボックスモード
        cwd: 作業ディレクトリ
        timeout: 全体タイムアウト秒数
    """
    task_list = [t.strip() for t in tasks.strip().split("\n") if t.strip()]
    if not task_list:
        return "[エラー] タスクが空です"
    if len(task_list) > 6:
        return "[エラー] 並列タスクは最大6つまでです"

    # 全タスクを並列実行
    results = await asyncio.gather(*[
        run_codex(
            prompt=task, model=model, sandbox=sandbox,
            cwd=cwd or None, timeout=timeout,
        )
        for task in task_list
    ])

    # 結果をまとめる
    output_parts = [f"[並列実行完了] {len(task_list)}タスク\n"]
    for i, (task, result) in enumerate(zip(task_list, results)):
        mark = "✅" if result["success"] else "❌"
        output_parts.append(
            f"━━━ タスク{i+1} {mark} ━━━\n"
            f"指示: {task}\n"
            f"{result['content']}\n"
        )

    return "\n".join(output_parts)


@mcp.tool()
async def review(
    code: str,
    language: str = "python",
    focus: str = "bugs,security,performance,readability",
) -> str:
    """Codex CLI(GPT-5.4)にコードレビューを依頼。Adversarial Review Loopの実行部分。

    Args:
        code: レビュー対象のコード
        language: プログラミング言語
        focus: レビューの焦点（カンマ区切り: bugs,security,performance,readability）
    """
    prompt = f"""以下の{language}コードをレビューしてください。

フォーカス: {focus}

各問題を以下のフォーマットで報告:
- [CRITICAL] バグ・セキュリティ問題（即修正必要）
- [WARNING] パフォーマンス・潜在的問題（修正推奨）
- [INFO] コードスタイル・可読性（任意改善）

コード:
```{language}
{code}
```"""

    result = await run_codex(prompt=prompt, sandbox="read-only")
    if result["success"]:
        return f"[Codex Review] GPT-5.4によるレビュー結果\n\n{result['content']}"
    else:
        return f"[Codex Review エラー] {result['content']}"


@mcp.tool()
async def explain(
    code: str,
    language: str = "python",
    detail_level: str = "medium",
) -> str:
    """Codex CLI(GPT-5.4)にコードの解説・分析を依頼。

    Args:
        code: 解説対象のコード
        language: プログラミング言語
        detail_level: 詳細レベル（brief/medium/detailed）
    """
    detail_map = {
        "brief": "簡潔に1-2文で",
        "medium": "主要な処理の流れを中心に",
        "detailed": "各行の意味も含めて詳細に",
    }
    detail_instruction = detail_map.get(detail_level, detail_map["medium"])

    prompt = f"""以下の{language}コードを{detail_instruction}解説してください。日本語で回答。

```{language}
{code}
```"""

    result = await run_codex(prompt=prompt, sandbox="read-only")
    if result["success"]:
        return f"[Codex Explain]\n\n{result['content']}"
    else:
        return f"[Codex Explain エラー] {result['content']}"


@mcp.tool()
async def generate(
    description: str,
    language: str = "python",
    cwd: str = "",
    output_file: str = "",
) -> str:
    """Codex CLI(GPT-5.4)にコード生成を依頼。

    Args:
        description: 生成するコードの説明（日本語OK）
        language: プログラミング言語
        cwd: 作業ディレクトリ
        output_file: 出力ファイルパス（空の場合はコードを返す）
    """
    sandbox = "workspace-write" if output_file else "read-only"
    blocked = _enforce_sandbox("generate", sandbox)
    if blocked:
        return blocked

    if output_file:
        prompt = f"""{language}で以下を実装し、{output_file}に保存してください:
{description}"""
    else:
        prompt = f"""{language}で以下を実装してください。コードのみ出力:
{description}"""

    result = await run_codex(
        prompt=prompt, sandbox=sandbox, cwd=cwd or None,
    )
    if result["success"]:
        return f"[Codex Generate]\n\n{result['content']}"
    else:
        return f"[Codex Generate エラー] {result['content']}"


@mcp.tool()
async def discuss(
    topic: str,
    context: str = "",
) -> str:
    """Codex CLI(GPT-5.4)と対話的にアイデアを深掘り。別視点の意見を得る。

    Args:
        topic: 議論したいトピック
        context: 追加コンテキスト（現在の設計案、課題など）
    """
    prompt = f"""以下のトピックについて、ソフトウェアエンジニアの視点から意見・提案をください。
既存のアプローチの問題点、代替案、トレードオフを含めてください。日本語で回答。

トピック: {topic}

{"コンテキスト: " + context if context else ""}"""

    result = await run_codex(prompt=prompt, sandbox="read-only")
    if result["success"]:
        return f"[Codex Discussion] GPT-5.4の意見\n\n{result['content']}"
    else:
        return f"[Codex Discussion エラー] {result['content']}"


# =============================================================================
# セッション管理ツール
# =============================================================================

@mcp.tool()
async def session_continue(
    prompt: str,
    thread_id: str = "",
    model: str = DEFAULT_MODEL,
    sandbox: str = "workspace-write",
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """前回のCodexセッション（スレッド）を引き継いで継続実行。

    thread_idを省略すると最新のセッションを自動で引き継ぐ。

    Args:
        prompt: 続きの指示（日本語OK）
        thread_id: 引き継ぐスレッドID（省略時は最新）
        model: 使用モデル
        sandbox: サンドボックスモード
        timeout: タイムアウト秒数
    """
    # セッション解決
    if thread_id:
        session = sessions.get_by_thread(thread_id)
    else:
        session = sessions.get_latest()

    if not session:
        return (
            "[セッション継続エラー] 引き継ぎ可能なセッションがありません。\n"
            "先に execute または trace_execute を実行してください。"
        )

    # 継続プロンプトを構築
    continuation = (
        f"前回の実行（thread: {session.thread_id}）の続きです。\n"
        f"前回の指示: {session.prompt}\n"
        f"前回の結果概要: {session.summary}\n\n"
        f"続きの指示: {prompt}"
    )

    result = await run_codex(
        prompt=continuation, model=model, sandbox=sandbox,
        timeout=timeout,
    )

    label = "継続完了" if result["success"] else "エラー"
    header = (
        f"[Codex Session Continue] {label}\n"
        f"引き継ぎ元: {session.thread_id}\n\n"
    )
    return header + result["content"]


@mcp.tool()
async def session_list() -> str:
    """Codexセッション（スレッド）の履歴一覧を表示。session_continueで使用するthread_idを確認できる。"""
    return f"[Codex Sessions]\n\n{sessions.format_list()}"


# =============================================================================
# ステータス
# =============================================================================

@mcp.tool()
async def spawn_codex_agent(
    prompt: str,
    description: str = "",
    agent_type: str = "worker",
    cwd: str = "",
    model: str = DEFAULT_MODEL,
    sandbox: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Start a background Codex worker with a Claude Code-style lifecycle."""
    resolved_type = _normalize_agent_type(agent_type)
    resolved_sandbox = sandbox or _default_agent_sandbox(resolved_type)
    blocked = _enforce_sandbox("execute", resolved_sandbox)
    if blocked:
        return {"ok": False, "error": blocked}
    err = _validate(prompt, resolved_sandbox, model)
    if err:
        return {"ok": False, "error": err["content"]}

    agent = codex_agents.create(
        description=description,
        agent_type=resolved_type,
        model=model,
        sandbox=resolved_sandbox,
        cwd=cwd or None,
    )
    codex_agents.start(agent, prompt, timeout)
    return codex_agents.snapshot(agent)


@mcp.tool()
async def send_codex_agent_input(
    agent_id: str,
    message: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Continue an existing background Codex agent with a new instruction."""
    agent = codex_agents.get(agent_id)
    if not agent:
        return {"ok": False, "error": f"Unknown agent_id: {agent_id}"}
    try:
        codex_agents.start(agent, message, timeout)
    except ValueError as e:
        return {"ok": False, "error": str(e), "agent_id": agent_id}
    return codex_agents.snapshot(agent)


@mcp.tool()
async def wait_codex_agent(
    agent_id: str,
    timeout: int = 30,
) -> dict:
    """Wait for a background Codex agent to finish its current turn."""
    agent = codex_agents.get(agent_id)
    if not agent:
        return {"ok": False, "error": f"Unknown agent_id: {agent_id}"}
    return await codex_agents.wait(agent, timeout)


@mcp.tool()
async def list_codex_agents() -> dict:
    """List all tracked background Codex agents."""
    agents = [codex_agents.snapshot(agent) for agent in codex_agents.list_all()]
    return {
        "ok": True,
        "count": len(agents),
        "agents": agents,
    }


@mcp.tool()
async def close_codex_agent(agent_id: str) -> dict:
    """Close an idle Codex agent and keep its last known result."""
    agent = codex_agents.get(agent_id)
    if not agent:
        return {"ok": False, "error": f"Unknown agent_id: {agent_id}"}
    try:
        codex_agents.close(agent)
    except ValueError as e:
        return {"ok": False, "error": str(e), "agent_id": agent_id}
    return codex_agents.snapshot(agent)


@mcp.tool()
async def status() -> str:
    """Codex CLIの状態とセッション情報を確認"""
    try:
        codex_path = _find_codex()
        if not codex_path:
            return "[claude-code-codex-agents Error] Codex CLIが見つかりません"
        proc = await asyncio.create_subprocess_exec(
            codex_path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        version = stdout.decode().strip()

        config_path = Path.home() / ".codex" / "config.toml"
        auth_path = Path.home() / ".codex" / "auth.json"

        session_count = len(sessions.list_all())
        agent_list = codex_agents.list_all()
        active_agents = sum(1 for agent in agent_list if agent.current_task is not None)
        latest = sessions.get_latest()
        latest_info = (
            f"最新セッション: {latest.thread_id} ({latest.model})"
            if latest else "セッション: なし"
        )

        return f"""[claude-code-codex-agents Status]
Codex CLI: {version}
認証: {"✅ 認証済み" if auth_path.exists() else "❌ 未認証"}
設定ファイル: {"✅ 存在" if config_path.exists() else "❌ なし"}
デフォルトモデル: {DEFAULT_MODEL}
デフォルトサンドボックス: {DEFAULT_SANDBOX}
セッション数: {session_count}
エージェント数: {len(agent_list)} (running: {active_agents})
{latest_info}
ツール数: 15 (execute, trace_execute, parallel_execute, review, explain, generate, discuss, session_continue, session_list, spawn_codex_agent, send_codex_agent_input, wait_codex_agent, list_codex_agents, close_codex_agent, status)"""
    except Exception as e:
        return f"[claude-code-codex-agents Error] Codex CLI確認失敗: {e}"


if __name__ == "__main__":
    mcp.run()
