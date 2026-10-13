"""workspace/tests/test_agent_components.py

P4-1 单测：validate_pg + chunker + prompt_builder + orchestrator + snapshot

原则：
- 不调真 ollama（用 mock）
- 不动真 git（snapshot 用 tmp_path/.git init 隔离）
- 不连真 PG（validate_pg trial_install=False）
"""
from __future__ import annotations
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from workspace.agent.validate_pg import (
    validate_pg, ValidationResult, _regex_check, _strip_strings,
)
from workspace.agent.chunker import chunk_function_or_procedure, Chunk, ChunkError
from workspace.agent.prompt_builder import (
    build_full_prompt, build_user_prompt, extract_code, SYSTEM_PROMPT,
)
from workspace.agent.orchestrator import (
    Orchestrator, BatchResult, _make_placeholder, PLACEHOLDER_FAILED,
)
from workspace.agent.snapshot import Snapshot, SnapshotError
from workspace.agent import quota as quota_mod


# ============================================================
# validate_pg
# ============================================================

VALID_CODE = """
CREATE OR REPLACE FUNCTION add_one(n INTEGER)
  RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
  result INTEGER;
BEGIN
  result := n + 1;
  RETURN result;
END;
$$;
"""


class TestRegexCheck:
    def test_valid_code_no_errors(self):
        assert _regex_check(VALID_CODE) == []

    def test_missing_create(self):
        code = "INVALID\nBEGIN END;\nLANGUAGE plpgsql AS $$ $$;"
        errors = _regex_check(code)
        assert any("CREATE" in e for e in errors)

    def test_begin_end_mismatch(self):
        code = (
            "CREATE FUNCTION x() RETURNS void LANGUAGE plpgsql AS $$\n"
            "BEGIN\nBEGIN\nEND;\n$$;"
        )
        errors = _regex_check(code)
        assert any("BEGIN/END" in e for e in errors)

    def test_missing_language(self):
        code = "CREATE FUNCTION x() RETURNS void AS $$ BEGIN END END $$;"
        errors = _regex_check(code)
        assert any("LANGUAGE" in e for e in errors)

    def test_language_semicolon_wrong(self):
        """LANGUAGE plpgsql 行末带分号是格式错（LLM 常见输出错）。"""
        code = """CREATE OR REPLACE FUNCTION F_CALC_GRADE(n NUMERIC) RETURNS VARCHAR
LANGUAGE plpgsql;
BEGIN
  RETURN 'A';
END;"""
        errors = _regex_check(code)
        assert any("LANGUAGE" in e and "分号" in e for e in errors)

    def test_unbalanced_dollar_removed(self):
        """$$ 在 PG 里是函数体界，不检失衡。"""
        code = "CREATE FUNCTION x() RETURNS void LANGUAGE plpgsql AS $$$$ BEGIN END;"
        errors = _regex_check(code)
        # 失衡检查已删除，只检其他
        assert not any("Unbalanced" in e for e in errors)

    def test_function_without_returns(self):
        code = "CREATE FUNCTION x() LANGUAGE plpgsql AS $$ BEGIN END END $$;"
        errors = _regex_check(code)
        assert any("RETURNS" in e for e in errors)


class TestStripStrings:
    def test_quoted_strings_removed(self):
        code = "SELECT 'has BEGIN END here' FROM t"
        result = _strip_strings(code)
        assert "BEGIN" not in result

    def test_dollar_quoted_strings_kept(self):
        """$$ 是函数体界，不剥。"""
        code = "SELECT $$body BEGIN END$$ FROM t"
        result = _strip_strings(code)
        # $$...$$ 保留
        assert result.count("$$") == 2


class TestValidatePg:
    def test_valid_code_ok(self):
        result = validate_pg(VALID_CODE, trial_install=False)
        assert result.ok is True
        assert result.errors == []

    def test_empty_code_fails(self):
        result = validate_pg("", trial_install=False)
        assert result.ok is False
        assert "Empty" in result.errors[0]

    def test_invalid_code_returns_errors(self):
        code = "INVALID ORACLE CODE"
        result = validate_pg(code, trial_install=False)
        assert result.ok is False
        assert any("CREATE" in e for e in result.errors)

    def test_trial_install_skipped_when_psql_missing(self, monkeypatch):
        """无 psql 时 trial_install 跳过，不算失败。"""
        monkeypatch.delenv("PSQL_BIN", raising=False)
        with patch("workspace.agent.validate_pg._psql_available", return_value=False):
            result = validate_pg(VALID_CODE, trial_install=True)
        assert result.ok is True  # 仅跑正则


def errors_to_str(errors):
    return " ".join(errors)


# ============================================================
# chunker
# ============================================================

class TestChunker:
    def test_single_function(self):
        chunks = chunk_function_or_procedure(VALID_CODE)
        assert len(chunks) == 1
        assert chunks[0].object_name == "add_one"
        assert chunks[0].object_kind == "FUNCTION"
        assert chunks[0].code.strip() == VALID_CODE.strip()

    def test_multiple_functions(self):
        code = """
CREATE OR REPLACE FUNCTION foo()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  PERFORM bar();
END;
$$;

CREATE OR REPLACE PROCEDURE bar()
LANGUAGE plpgsql
AS $$
BEGIN
  -- body
END;
$$;
"""
        chunks = chunk_function_or_procedure(code)
        assert len(chunks) == 2
        assert chunks[0].object_name == "foo"
        assert chunks[0].object_kind == "FUNCTION"
        assert chunks[1].object_name == "bar"
        assert chunks[1].object_kind == "PROCEDURE"
        # 第二个 chunk 应有 context
        assert chunks[1].context_before != ""

    def test_empty_raises(self):
        with pytest.raises(ChunkError):
            chunk_function_or_procedure("")

    def test_no_function_raises(self):
        with pytest.raises(ChunkError):
            chunk_function_or_procedure("-- comment only\nSELECT 1;")

    def test_too_long_function_raises(self):
        long_body = "\n".join(["SELECT 1;"] * 600)
        code = f"""
CREATE FUNCTION foo() RETURNS void LANGUAGE plpgsql AS $$
BEGIN
{long_body}
END;
$$;
"""
        with pytest.raises(ChunkError) as exc_info:
            chunk_function_or_procedure(code)
        assert "too long" in str(exc_info.value)


# ============================================================
# prompt_builder
# ============================================================

class TestPromptBuilder:
    def test_system_prompt_present(self):
        prompt = build_full_prompt("CREATE FUNCTION x() ...")
        assert "Oracle" in prompt or "PostgreSQL" in prompt
        # SYSTEM_PROMPT 应是开头
        assert prompt.startswith(SYSTEM_PROMPT.strip()[:20])

    def test_user_prompt_with_context(self):
        prompt = build_user_prompt("source code", "previous tail")
        assert "previous tail" in prompt
        assert "source code" in prompt

    def test_user_prompt_no_context(self):
        prompt = build_user_prompt("source code", "")
        assert "无" in prompt  # 中文无上下文标识
        assert "source code" in prompt

    def test_extract_code_from_markdown(self):
        llm = "```pgsql\nCREATE FUNCTION x() RETURNS void LANGUAGE plpgsql AS $$ BEGIN END END $$;\n```"
        result = extract_code(llm)
        assert "CREATE FUNCTION" in result
        assert "```" not in result

    def test_extract_code_no_markdown(self):
        llm = "CREATE FUNCTION x() RETURNS void LANGUAGE plpgsql AS $$ BEGIN END END $$;"
        result = extract_code(llm)
        assert result == llm.strip()

    def test_extract_code_multiline(self):
        llm = "```sql\nCREATE FUNCTION x()\nRETURNS void\nLANGUAGE plpgsql\nAS $$\nBEGIN\nEND;\n$$;\n```"
        result = extract_code(llm)
        assert "BEGIN" in result
        assert "```" not in result


# ============================================================
# orchestrator
# ============================================================

@pytest.fixture
def mock_ollama():
    """Mock OllamaClient，让 generate 返回 valid PG code。"""
    with patch("workspace.agent.orchestrator.OllamaClient") as MockClass:
        instance = MagicMock()
        instance.generate.return_value = (
            "```pgsql\n"
            + VALID_CODE.replace("add_one", "add_one_v2")
            + "\n```"
        )
        MockClass.return_value = instance
        yield instance


@pytest.fixture
def reset_quota_fixture(tmp_path, monkeypatch):
    """每个测试重置配额。"""
    quota_mod._instance = None
    counter_path = tmp_path / ".quota_counter"
    def fake_get_quota():
        return quota_mod._QuotaManager(counter_path, max_calls=5)
    monkeypatch.setattr(quota_mod, "get_quota", fake_get_quota)
    import workspace.agent.orchestrator as oc
    monkeypatch.setattr(oc, "get_quota", fake_get_quota)
    # 返回单例函数，让测试用 reset_quota_fixture()
    return fake_get_quota


class TestOrchestrator:
    def test_process_chunk_success(self, mock_ollama, reset_quota_fixture):
        oc = Orchestrator(validate_kwargs={"trial_install": False})
        chunk = Chunk(index=0, code=VALID_CODE, object_name="add_one", object_kind="FUNCTION")
        result = oc.process_chunk(chunk)
        assert result.success is True
        assert "add_one_v2" in result.new_code
        assert result.quota_used == 1

    def test_process_chunk_llm_error(self, reset_quota_fixture):
        from workspace.agent.llm_client import LLMError
        with patch("workspace.agent.orchestrator.OllamaClient") as MockClass:
            instance = MagicMock()
            instance.generate.side_effect = LLMError("bad arg from LLM")
            MockClass.return_value = instance

            oc = Orchestrator(validate_kwargs={"trial_install": False})
            chunk = Chunk(index=0, code=VALID_CODE, object_name="x", object_kind="FUNCTION")
            result = oc.process_chunk(chunk)
            assert result.success is False
            assert "RAISE EXCEPTION" in result.new_code  # 占位
            assert "bad arg from LLM" in (result.errors[0] if result.errors else "")

    def test_process_chunk_validation_failure(self, mock_ollama, reset_quota_fixture):
        """LLM 返回的代码验证失败 → 占位回灌。"""
        mock_ollama.generate.return_value = (
            "```pgsql\nINVALID ORACLE GARBAGE\n```"
        )
        oc = Orchestrator(validate_kwargs={"trial_install": False})
        chunk = Chunk(index=0, code=VALID_CODE, object_name="x", object_kind="FUNCTION")
        result = oc.process_chunk(chunk)
        assert result.success is False
        assert "RAISE EXCEPTION" in result.new_code

    def test_process_chunk_quota_exceeded(self, reset_quota_fixture):
        q = quota_mod.get_quota()
        for _ in range(q.max_calls):
            q.inc()
        q = quota_mod.get_quota()
        for _ in range(q.max_calls):
            q.inc()

        oc = Orchestrator(validate_kwargs={"trial_install": False})
        chunk = Chunk(index=0, code=VALID_CODE, object_name="x", object_kind="FUNCTION")
        result = oc.process_chunk(chunk)
        assert result.success is False
        assert "quota" in result.errors[0].lower()
        assert "RAISE EXCEPTION" in result.new_code

    def test_process_sql_file(self, mock_ollama, reset_quota_fixture, tmp_path):
        sql = VALID_CODE
        f = tmp_path / "test.sql"
        f.write_text(sql, encoding="utf-8")

        oc = Orchestrator(validate_kwargs={"trial_install": False})
        results = oc.process_sql_file(f)
        assert len(results) == 1
        assert results[0].success is True


class TestPlaceholder:
    def test_placeholder_returns_valid_pg(self):
        code = _make_placeholder("foo_bar", "test reason")
        assert "CREATE OR REPLACE FUNCTION" in code
        assert "RAISE EXCEPTION" in code
        assert "foo_bar" in code
        # 验证 placeholder 本身能过 validate_pg 正则层
        result = validate_pg(code, trial_install=False)
        assert result.ok is True

    def test_placeholder_with_special_chars_in_error(self):
        code = _make_placeholder("x", "error with 'quote' and ;")
        assert "quote" in code
        # 单引号已转义
        assert "''quote''" in code


# ============================================================
# snapshot
# ============================================================

@pytest.fixture
def fake_git_repo(tmp_path, monkeypatch):
    """建一个临时 git 仓库，让 Snapshot 指它。"""
    subprocess.run(
        ["git", "init", "-q"],
        cwd=str(tmp_path),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        check=True,
    )
    (tmp_path / "test.txt").write_text("v1")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "-q"],
        cwd=str(tmp_path),
        check=True,
    )
    return tmp_path


class TestSnapshot:
    def test_create_returns_sha(self, fake_git_repo):
        (fake_git_repo / "test.txt").write_text("v2")
        sm = Snapshot(repo_root=fake_git_repo)
        sha = sm.create("second commit")
        assert len(sha) == 40  # SHA1 hex

    def test_restore_specific_file(self, fake_git_repo):
        (fake_git_repo / "test.txt").write_text("v2")
        sm = Snapshot(repo_root=fake_git_repo)
        sha = sm.create("v2 commit")

        (fake_git_repo / "test.txt").write_text("v3")
        sm.restore(sha, files=["test.txt"])

        assert (fake_git_repo / "test.txt").read_text() == "v2"

    def test_restore_all(self, fake_git_repo):
        (fake_git_repo / "test.txt").write_text("v2")
        sm = Snapshot(repo_root=fake_git_repo)
        sha = sm.create("v2 commit")

        (fake_git_repo / "test.txt").write_text("v3")
        sm.restore(sha)

        assert (fake_git_repo / "test.txt").read_text() == "v2"

    def test_current_sha(self, fake_git_repo):
        sm = Snapshot(repo_root=fake_git_repo)
        sha = sm.current_sha()
        assert len(sha) == 40