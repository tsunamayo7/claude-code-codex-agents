"""helix-codex テストスイート

モックJSONLを使用してCodex CLIに依存しないユニットテスト。
"""

import asyncio
import json
import time

import pytest

# server.pyからインポート
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from server import (
    CodexTrace,
    SessionManager,
    _validate,
    _enforce_sandbox,
    parse_jsonl_events,
    VALID_SANDBOXES,
)


# =============================================================================
# フィクスチャ: モックJSONLデータ
# =============================================================================

def make_jsonl(*events: dict) -> str:
    """複数のJSONオブジェクトをJSONL文字列に変換"""
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events)


MOCK_THREAD_STARTED = {"type": "thread.started", "thread_id": "thread_test123"}
MOCK_THREAD_STARTED_CAMEL = {"type": "thread.started", "threadId": "thread_camel456"}

MOCK_AGENT_MESSAGE = {
    "type": "item.completed",
    "item": {
        "type": "agent_message",
        "text": "バグを修正しました。",
    },
}

MOCK_NORMAL_MESSAGE = {
    "type": "item.completed",
    "item": {
        "type": "message",
        "content": [
            {"type": "text", "text": "認証ロジックを改善しました。"},
        ],
    },
}

MOCK_TOOL_READ_FILE = {
    "type": "item.completed",
    "item": {
        "type": "function_call",
        "name": "read_file",
        "status": "completed",
        "arguments": json.dumps({"path": "src/auth.py"}),
    },
}

MOCK_TOOL_EDIT_FILE = {
    "type": "item.completed",
    "item": {
        "type": "function_call",
        "name": "edit_file",
        "status": "completed",
        "arguments": json.dumps({"file_path": "src/auth.py"}),
    },
}

MOCK_TOOL_BASH = {
    "type": "item.completed",
    "item": {
        "type": "function_call",
        "name": "bash",
        "status": "completed",
        "arguments": json.dumps({"command": "python -m pytest tests/ -v"}),
    },
}

MOCK_TOOL_CREATED = {
    "type": "item.created",
    "item": {
        "type": "function_call",
        "name": "read_file",
    },
}

MOCK_TURN_COMPLETED = {
    "type": "turn.completed",
    "summary": "ファイルを読み取り、修正を適用しました。",
}

MOCK_ERROR = {
    "type": "error",
    "message": "Rate limit exceeded",
}


# =============================================================================
# テスト: バリデーション
# =============================================================================

class TestValidation:
    """入力バリデーションのテスト"""

    def test_valid_inputs(self):
        assert _validate("hello", "read-only", "gpt-5.4") is None
        assert _validate("テスト", "workspace-write", "gpt-5.4") is None
        assert _validate("x", "danger-full-access", "gpt-5.4") is None

    def test_invalid_sandbox(self):
        result = _validate("hello", "invalid-mode", "gpt-5.4")
        assert result is not None
        assert result["success"] is False
        assert "無効なsandbox" in result["content"]

    def test_empty_prompt(self):
        result = _validate("", "read-only", "gpt-5.4")
        assert result is not None
        assert result["success"] is False
        assert "プロンプトが空" in result["content"]

    def test_whitespace_only_prompt(self):
        result = _validate("   \n\t  ", "read-only", "gpt-5.4")
        assert result is not None
        assert result["success"] is False

    def test_valid_sandboxes_constant(self):
        assert "read-only" in VALID_SANDBOXES
        assert "workspace-write" in VALID_SANDBOXES
        assert "danger-full-access" in VALID_SANDBOXES
        assert len(VALID_SANDBOXES) == 3


# =============================================================================
# テスト: セキュリティポリシー
# =============================================================================

class TestSecurityPolicy:
    """sandbox制限のテスト"""

    def test_readonly_blocks_execute(self):
        result = _enforce_sandbox("execute", "read-only")
        assert result is not None
        assert "セキュリティ" in result

    def test_readonly_blocks_generate(self):
        result = _enforce_sandbox("generate", "read-only")
        assert result is not None

    def test_readonly_allows_review(self):
        result = _enforce_sandbox("review", "read-only")
        assert result is None

    def test_workspace_allows_execute(self):
        result = _enforce_sandbox("execute", "workspace-write")
        assert result is None

    def test_workspace_allows_generate(self):
        result = _enforce_sandbox("generate", "workspace-write")
        assert result is None

    def test_danger_allows_all(self):
        assert _enforce_sandbox("execute", "danger-full-access") is None
        assert _enforce_sandbox("generate", "danger-full-access") is None
        assert _enforce_sandbox("review", "danger-full-access") is None

    def test_unknown_sandbox_passes(self):
        # ポリシーに定義されていないsandboxは通す（バリデーション側で弾く）
        result = _enforce_sandbox("execute", "nonexistent")
        assert result is None


# =============================================================================
# テスト: JSONLパース
# =============================================================================

class TestJsonlParsing:
    """JSONLイベント解析のテスト"""

    def test_thread_started_snake_case(self):
        """thread_id (snake_case) 形式"""
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_THREAD_STARTED), trace)
        assert trace.thread_id == "thread_test123"

    def test_thread_started_camel_case(self):
        """threadId (camelCase) 形式にも対応"""
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_THREAD_STARTED_CAMEL), trace)
        assert trace.thread_id == "thread_camel456"

    def test_agent_message(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_AGENT_MESSAGE), trace)
        assert len(trace.messages) == 1
        assert "バグを修正しました" in trace.messages[0]

    def test_normal_message(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_NORMAL_MESSAGE), trace)
        assert len(trace.messages) == 1
        assert "認証ロジック" in trace.messages[0]

    def test_tool_calls_tracked(self):
        trace = CodexTrace(started_at=time.time())
        events = make_jsonl(MOCK_TOOL_READ_FILE, MOCK_TOOL_EDIT_FILE, MOCK_TOOL_BASH)
        parse_jsonl_events(events, trace)
        assert len(trace.tool_calls) == 3
        assert trace.tool_calls[0]["name"] == "read_file"
        assert trace.tool_calls[1]["name"] == "edit_file"
        assert trace.tool_calls[2]["name"] == "bash"

    def test_file_tracking(self):
        trace = CodexTrace(started_at=time.time())
        events = make_jsonl(MOCK_TOOL_READ_FILE, MOCK_TOOL_EDIT_FILE)
        parse_jsonl_events(events, trace)
        assert "src/auth.py" in trace.files_touched

    def test_bash_command_detail(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_TOOL_BASH), trace)
        assert "pytest" in trace.tool_calls[0]["detail"]

    def test_tool_created_event(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_TOOL_CREATED), trace)
        # tool_startイベントが追加される
        tool_starts = [e for e in trace.events if e.event_type == "tool_start"]
        assert len(tool_starts) == 1

    def test_turn_completed_fallback(self):
        """メッセージがない場合、turn.completedのsummaryがフォールバック"""
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_TURN_COMPLETED), trace)
        assert len(trace.messages) == 1
        assert "修正を適用" in trace.messages[0]

    def test_turn_completed_no_override(self):
        """メッセージがある場合、summaryは追加されない"""
        trace = CodexTrace(started_at=time.time())
        events = make_jsonl(MOCK_AGENT_MESSAGE, MOCK_TURN_COMPLETED)
        parse_jsonl_events(events, trace)
        assert len(trace.messages) == 1
        assert "バグを修正" in trace.messages[0]

    def test_error_event(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(MOCK_ERROR), trace)
        assert len(trace.errors) == 1
        assert "Rate limit" in trace.errors[0]

    def test_invalid_json_skipped_and_recorded(self):
        """不正なJSON行はスキップされ、エラーに記録される"""
        trace = CodexTrace(started_at=time.time())
        jsonl = "not json\n" + json.dumps(MOCK_THREAD_STARTED)
        parse_jsonl_events(jsonl, trace)
        assert trace.thread_id == "thread_test123"
        # 不正行がエラーとして記録される
        assert any("スキップ" in e for e in trace.errors)

    def test_empty_input(self):
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events("", trace)
        assert trace.thread_id is None
        assert len(trace.messages) == 0

    def test_full_session(self):
        """完全なセッションフローのテスト"""
        trace = CodexTrace(started_at=time.time())
        events = make_jsonl(
            MOCK_THREAD_STARTED,
            MOCK_TOOL_CREATED,
            MOCK_TOOL_READ_FILE,
            MOCK_TOOL_EDIT_FILE,
            MOCK_TOOL_BASH,
            MOCK_AGENT_MESSAGE,
            MOCK_TURN_COMPLETED,
        )
        parse_jsonl_events(events, trace)

        assert trace.thread_id == "thread_test123"
        assert len(trace.tool_calls) == 3
        assert len(trace.messages) == 1
        assert "src/auth.py" in trace.files_touched

    def test_malformed_arguments(self):
        """argumentsがJSON以外でもクラッシュしない"""
        event = {
            "type": "item.completed",
            "item": {
                "type": "function_call",
                "name": "test_tool",
                "status": "completed",
                "arguments": "not-json-string",
            },
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0]["name"] == "test_tool"


# =============================================================================
# テスト: CodexTrace レポート生成
# =============================================================================

class TestCodexTraceReport:
    """構造化レポート生成のテスト"""

    def test_basic_report(self):
        trace = CodexTrace(
            thread_id="thread_abc",
            model="gpt-5.4",
            started_at=100.0,
            ended_at=108.3,
            messages=["修正完了"],
        )
        report = trace.format_report()
        assert "8.3秒" in report
        assert "thread_abc" in report
        assert "修正完了" in report

    def test_report_with_tools(self):
        trace = CodexTrace(
            started_at=100.0,
            ended_at=105.0,
            tool_calls=[
                {"name": "read_file", "status": "completed", "detail": "src/main.py"},
                {"name": "bash", "status": "completed", "detail": "pytest"},
            ],
            messages=["OK"],
        )
        report = trace.format_report()
        assert "ツール使用 (2回)" in report
        assert "read_file" in report
        assert "src/main.py" in report

    def test_report_with_files(self):
        trace = CodexTrace(
            started_at=0, ended_at=1,
            files_touched=["a.py", "b.py", "a.py"],  # 重複あり
            messages=["done"],
        )
        report = trace.format_report()
        assert "ファイル操作 (2件)" in report  # 重複除去

    def test_report_with_errors(self):
        trace = CodexTrace(
            started_at=0, ended_at=1,
            errors=["Rate limit exceeded"],
        )
        report = trace.format_report()
        assert "エラー (1件)" in report
        assert "Rate limit" in report

    def test_verbose_report(self):
        trace = CodexTrace(started_at=100.0, ended_at=105.0)
        trace.add_event("thread.started", {})
        trace.add_event("item.completed", {})
        report = trace.format_report(verbose=True)
        assert "イベントログ (2件)" in report

    def test_nonverbose_no_eventlog(self):
        trace = CodexTrace(started_at=100.0, ended_at=105.0)
        trace.add_event("thread.started", {})
        report = trace.format_report(verbose=False)
        assert "イベントログ" not in report

    def test_elapsed_property(self):
        trace = CodexTrace(started_at=100.0, ended_at=112.5)
        assert trace.elapsed == 12.5

    def test_elapsed_zero_when_not_ended(self):
        trace = CodexTrace(started_at=100.0, ended_at=0.0)
        assert trace.elapsed == 0.0


# =============================================================================
# テスト: セッション管理
# =============================================================================

class TestSessionManager:
    """セッション管理のテスト"""

    def test_record_and_retrieve(self):
        mgr = SessionManager()
        trace = CodexTrace(
            thread_id="thread_001",
            model="gpt-5.4",
            started_at=time.time() - 10,
            ended_at=time.time(),
            messages=["完了しました"],
            tool_calls=[{"name": "bash", "status": "completed", "detail": ""}],
            files_touched=["test.py"],
        )
        mgr.record(trace, "テストタスク")

        latest = mgr.get_latest()
        assert latest is not None
        assert latest.thread_id == "thread_001"
        assert latest.model == "gpt-5.4"
        assert latest.tool_count == 1
        assert "test.py" in latest.files_touched

    def test_get_by_thread(self):
        mgr = SessionManager()
        for i in range(3):
            trace = CodexTrace(
                thread_id=f"thread_{i}",
                model="gpt-5.4",
                started_at=time.time(),
                ended_at=time.time(),
                messages=[f"result_{i}"],
            )
            mgr.record(trace, f"task_{i}")

        s = mgr.get_by_thread("thread_1")
        assert s is not None
        assert s.thread_id == "thread_1"

    def test_get_by_thread_not_found(self):
        mgr = SessionManager()
        assert mgr.get_by_thread("nonexistent") is None

    def test_max_sessions(self):
        mgr = SessionManager(max_sessions=3)
        for i in range(5):
            trace = CodexTrace(
                thread_id=f"thread_{i}",
                model="gpt-5.4",
                started_at=time.time(),
                ended_at=time.time(),
            )
            mgr.record(trace, f"task_{i}")

        all_sessions = mgr.list_all()
        assert len(all_sessions) == 3
        # 最新3つが残る
        assert all_sessions[0].thread_id == "thread_4"

    def test_no_record_without_thread_id(self):
        mgr = SessionManager()
        trace = CodexTrace(
            thread_id=None,
            model="gpt-5.4",
            started_at=time.time(),
            ended_at=time.time(),
        )
        mgr.record(trace, "no thread")
        assert mgr.get_latest() is None

    def test_format_list_empty(self):
        mgr = SessionManager()
        assert "なし" in mgr.format_list()

    def test_format_list_with_sessions(self):
        mgr = SessionManager()
        trace = CodexTrace(
            thread_id="thread_fmt",
            model="gpt-5.4",
            started_at=time.time() - 30,
            ended_at=time.time(),
            messages=["done"],
            tool_calls=[{"name": "x", "status": "completed", "detail": ""}],
        )
        mgr.record(trace, "フォーマットテスト")
        output = mgr.format_list()
        assert "thread_fmt" in output
        assert "gpt-5.4" in output

    def test_session_success_flag(self):
        mgr = SessionManager()
        # 成功ケース
        trace_ok = CodexTrace(
            thread_id="ok", model="gpt-5.4",
            started_at=time.time(), ended_at=time.time(),
        )
        mgr.record(trace_ok, "ok")
        assert mgr.get_latest().success is True

        # 失敗ケース
        trace_err = CodexTrace(
            thread_id="err", model="gpt-5.4",
            started_at=time.time(), ended_at=time.time(),
            errors=["something went wrong"],
        )
        mgr.record(trace_err, "err")
        assert mgr.get_latest().success is False


# =============================================================================
# テスト: エッジケース
# =============================================================================

class TestEdgeCases:
    """境界値・エッジケースのテスト"""

    def test_very_long_message(self):
        """非常に長いメッセージの処理"""
        long_text = "x" * 10000
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": long_text},
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        assert len(trace.messages[0]) == 10000

    def test_unicode_in_messages(self):
        """日本語・絵文字を含むメッセージ"""
        event = {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "修正完了 🎉 テスト通過"},
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        assert "🎉" in trace.messages[0]

    def test_multiple_messages(self):
        """複数メッセージの蓄積"""
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": f"msg_{i}"}}
            for i in range(5)
        ]
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(*events), trace)
        assert len(trace.messages) == 5

    def test_empty_content_array(self):
        """contentが空配列"""
        event = {
            "type": "item.completed",
            "item": {"type": "message", "content": []},
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        assert len(trace.messages) == 0

    def test_arguments_as_non_string(self):
        """argumentsがdict（通常はstr）の場合"""
        event = {
            "type": "item.completed",
            "item": {
                "type": "function_call",
                "name": "test",
                "status": "completed",
                "arguments": {"path": "test.py"},  # dictの場合
            },
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        # クラッシュしないこと
        assert len(trace.tool_calls) == 1

    def test_session_prompt_truncation(self):
        """長いプロンプトが200文字で切り詰められる"""
        mgr = SessionManager()
        long_prompt = "a" * 500
        trace = CodexTrace(
            thread_id="trunc", model="gpt-5.4",
            started_at=time.time(), ended_at=time.time(),
        )
        mgr.record(trace, long_prompt)
        assert len(mgr.get_latest().prompt) == 200

    def test_non_dict_json_skipped(self):
        """JSON配列や文字列はスキップされる"""
        trace = CodexTrace(started_at=time.time())
        jsonl = '[1,2,3]\n"just a string"\n123'
        parse_jsonl_events(jsonl, trace)
        assert len(trace.messages) == 0
        assert any("3行スキップ" in e for e in trace.errors)

    def test_non_dict_item_skipped(self):
        """item がdictでない場合にクラッシュしない"""
        event = {
            "type": "item.completed",
            "item": "not a dict",
        }
        trace = CodexTrace(started_at=time.time())
        parse_jsonl_events(make_jsonl(event), trace)
        assert len(trace.messages) == 0
        assert len(trace.tool_calls) == 0


# =============================================================================
# テスト: セキュリティ（制御文字サニタイズ）
# =============================================================================

class TestSanitization:
    """terminal injection対策のテスト"""

    def test_ansi_escape_removed(self):
        from server import _sanitize
        assert _sanitize("hello\x1b[31mRED\x1b[0m world") == "helloRED world"

    def test_null_bytes_removed(self):
        from server import _sanitize
        assert _sanitize("hello\x00world") == "helloworld"

    def test_osc_sequence_removed(self):
        from server import _sanitize
        assert _sanitize("text\x1b]0;evil title\x07more") == "textmore"

    def test_normal_text_preserved(self):
        from server import _sanitize
        text = "普通のテキスト 🎉 with numbers 123"
        assert _sanitize(text) == text

    def test_sanitize_in_report(self):
        """format_reportがサニタイズされた出力を返す"""
        trace = CodexTrace(
            started_at=100.0, ended_at=105.0,
            messages=["result\x1b[31m injected\x1b[0m"],
        )
        report = trace.format_report()
        assert "\x1b[" not in report
        assert "result injected" in report
