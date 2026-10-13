"""
D0: Defect-1 + Defect-2 修复（install_oracle_poc.py）

Defect-1：产物无 search_path 隔离
  - 修复：install_oracle_poc.py 默认 CREATE SCHEMA + SET search_path
  - 产物 00_CREATE_ALL_PG.sql 现在头部也带同样设置
  - 单测验证 run_psql 与 cmd_install 正确发出 SET search_path

Defect-2：psql \\ir 嵌套子文件 ON_ERROR_STOP 不生效
  - 修复：install_oracle_poc.py 用 per-file psql -f 调用（不嵌套）
  - 单文件错立即 STOP（默认），可 --keep-going 收集全错误
  - 单测：单文件错→ exit != 0，缺 keep-going→后续不跑
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# 让 scripts/ 模块可 import
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import install_oracle_poc as iop  # noqa: E402


def test_explicit_wins():
    """显式传 psql 路径优先"""
    with patch("pathlib.Path.exists", return_value=True):
        r = iop.find_psql("/explicit/path/x.py")
    assert r == "/explicit/path/x.py"


def test_env_fallback(monkeypatch, tmp_path):
    """env 变量 PSQL_BIN 第二优先（DEFAULT_PSQL 不存在时）"""
    monkeypatch.setattr(iop, "DEFAULT_PSQL", "/no/such/default")
    # 创建一个真实存在文件让 .exists() True
    fake_psql = tmp_path / "psql"
    fake_psql.write_text("")
    monkeypatch.setenv("PSQL_BIN", str(fake_psql))
    r = iop.find_psql(None)
    assert r == str(fake_psql)


def test_path_fallback(monkeypatch):
    """PATH 第三优先：纯名路径走 which 分支"""
    monkeypatch.delenv("PSQL_BIN", raising=False)
    # DEFAULT_PSQL 纯名 "psql" → 不含 / → 走 shutil.which 分支
    monkeypatch.setattr(iop, "DEFAULT_PSQL", "psql")
    with patch("shutil.which", return_value="/usr/bin/psql") as mock_which:
        r = iop.find_psql(None)
    # 返回值是 candidate 本身（'psql'），不是 which 的展开路径
    assert r == "psql"
    mock_which.assert_called_with("psql")


def test_not_found_raises(monkeypatch):
    """找不到应抛 FileNotFoundError"""
    monkeypatch.delenv("PSQL_BIN", raising=False)
    with patch("pathlib.Path.exists", return_value=False):
        with patch("shutil.which", return_value=None):
            import pytest
            with pytest.raises(FileNotFoundError):
                iop.find_psql(None)


def test_returns_in_declared_order(tmp_path):
    """按 INSTALL_ORDER 排序"""
    for sub in ["DATA", "TABLE", "FUNCTION", "VIEW"]:
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "x.sql").write_text("-- x")
    files = iop.ordered_sql_files(tmp_path)
    names = [f.parent.name for f in files]
    # TABLE 应在 FUNCTION 前
    assert names.index("TABLE") < names.index("FUNCTION")


def test_skips_nonexistent_dirs(tmp_path):
    """不存在的目录跳过"""
    (tmp_path / "TABLE").mkdir()
    (tmp_path / "TABLE" / "a.sql").write_text("-- a")
    files = iop.ordered_sql_files(tmp_path)
    assert len(files) == 1


def test_emits_set_search_path():
    """Defect-1：run_psql 必须把 schema 注入到子文件调用前"""
    args = MagicMock(
        psql="/bin/psql",
        host="localhost",
        port=5432,
        user="postgres",
        dbname="postgres",
        password=None,
        schema="my_test_schema",
    )
    sql_file = Path("/tmp/x.sql")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        iop.run_psql(args, sql_file)

    cmd = mock_run.call_args[0][0]
    idx_c = cmd.index("-c")
    assert "SET search_path" in cmd[idx_c + 1]
    assert "my_test_schema" in cmd[idx_c + 1]


def test_uses_ON_ERROR_STOP():
    """Defect-2：每次 -f 调用必须 ON_ERROR_STOP=on（per-file 关键）"""
    args = MagicMock(
        psql="/bin/psql", host="h", port=5432, user="u", dbname="d",
        password=None,
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        iop.run_psql(args, Path("/tmp/x.sql"))

    cmd = mock_run.call_args[0][0]
    idx_v = cmd.index("-v")
    assert "ON_ERROR_STOP=on" in cmd[idx_v + 1]


def test_default_stops_on_first_failure(tmp_path):
    """Defect-2：默认错立即停"""
    for sub in ["TABLE", "SEQUENCE", "TYPE"]:
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "x.sql").write_text("-- x")

    args = MagicMock(
        psql="/bin/psql",
        host="localhost",
        port=5432,
        user="postgres",
        dbname="postgres",
        password=None,
        schema="test_schema",
        dst=str(tmp_path),
        keep_going=False,
    )

    call_count = {"n": 0}

    def fake_run(cmd, **kw):
        r = MagicMock()
        cmd_str = " ".join(str(s) for s in cmd)
        call_count["n"] += 1
        if "CREATE SCHEMA" in cmd_str:
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
        else:
            r.returncode = 3
            r.stdout = ""
            r.stderr = "错误:  i_vsql 不存在"
        return r

    with patch("subprocess.run", side_effect=fake_run):
        rc = iop.cmd_install(args)

    # setup + 1 个 fail 文件后停 = 2 次
    assert rc == 1
    assert call_count["n"] == 2


def test_keep_going_collects_all_errors(tmp_path):
    """Defect-2：--keep-going 收集全部错误"""
    for sub in ["TABLE", "SEQUENCE", "TYPE"]:
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "x.sql").write_text("-- x")

    args = MagicMock(
        psql="/bin/psql",
        host="localhost",
        port=5432,
        user="postgres",
        dbname="postgres",
        password=None,
        schema="test_schema",
        dst=str(tmp_path),
        keep_going=True,
    )

    def fake_run(cmd, **kw):
        r = MagicMock()
        cmd_str = " ".join(str(s) for s in cmd)
        # setup CREATE SCHEMA 成功
        if "CREATE SCHEMA" in cmd_str:
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
        else:
            r.returncode = 3
            r.stdout = ""
            r.stderr = "错误"
        return r

    with patch("subprocess.run", side_effect=fake_run):
        rc = iop.cmd_install(args)

    # 3 个文件全失败，keep_going 跑到最后
    assert rc == 1


def test_dst_must_exist():
    """dst 不存在应早返 exit 2"""
    args = MagicMock(
        psql="/bin/psql",
        host="localhost",
        port=5432,
        user="postgres",
        dbname="postgres",
        password=None,
        schema="test_schema",
        dst="/no/such/dir",
        keep_going=False,
    )
    rc = iop.cmd_install(args)
    assert rc == 2