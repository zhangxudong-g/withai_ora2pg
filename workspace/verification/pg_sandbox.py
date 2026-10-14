"""
Level 3: PostgreSQL 沙箱执行

把转换后的 DDL / PL-SQL 真正装进一个隔离 schema，记录每个对象的安装结果。

相比原 `PGExecutor` 的改进：
1. 连接复用：原实现每个语句、每个文件都 `psycopg2.connect()` 并 `DROP SCHEMA`，
   一个 400 文件的项目会产生上千次连接和 schema 重建。现在一个 suite 只建一次 schema。
2. 覆盖全部对象类型：原实现只认 `$$ LANGUAGE` 的 FUNCTION/PROCEDURE，
   TABLE/TYPE/VIEW/TRIGGER/SEQUENCE 会被静默忽略。
3. 错误带 SQLSTATE，便于区分"语法错"(42601) 和"依赖缺失"(42703/42P01)。
4. autocommit：单条语句失败不会污染后续语句的事务状态。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .models import ObjectInstall
from .sqlsplit import classify, extract_object_name, split_statements

DEFAULT_PG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": "postgres",
}

_NAME_LOWER_RE = re.compile(r"[A-Z]")


def to_pg_name(name: str) -> str:
    """Oracle 大写标识符 -> PG 小写（未加引号时会自动折叠为小写）"""
    return _NAME_LOWER_RE.sub(lambda m: m.group(0).lower(), name)


class PGSandbox:
    """
    隔离 schema 的 PG 执行器

    用法:
        with PGSandbox(schema="ora2pg_verify") as sb:
            sb.reset()
            sb.install_script(ddl_sql)
            print(sb.scalar("SELECT count(*) FROM t"))
    """

    def __init__(self, schema: str = "ora2pg_verify", conn: Optional[Dict[str, Any]] = None):
        self.schema = schema
        self.conn_params = {**DEFAULT_PG, **(conn or {})}
        self._conn = None
        self._cur = None

    # ---------------- 连接生命周期 ----------------

    def connect(self) -> "PGSandbox":
        import psycopg2
        if self._conn is None:
            self._conn = psycopg2.connect(**self.conn_params)
            self._conn.autocommit = True
            self._cur = self._conn.cursor()
        return self

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
                self._cur = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    def available(self) -> Tuple[bool, str]:
        """探测 PG 是否可用（供 pipeline 优雅降级）"""
        try:
            self.connect()
            self._cur.execute("SELECT 1")
            self._cur.fetchone()
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    # ---------------- schema 生命周期 ----------------

    def reset(self):
        """重建 schema（一个 suite 只调一次）"""
        c = self.connect()
        c._cur.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        c._cur.execute(f'CREATE SCHEMA "{self.schema}"')
        c._cur.execute(f'SET search_path TO "{self.schema}", pkg_score_manage, public')
        return self

    def drop(self):
        try:
            c = self.connect()
            c._cur.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        except Exception:
            pass

    # ---------------- 执行 ----------------

    def execute(self, sql: str) -> Tuple[bool, str, str]:
        """执行一条 SQL，返回 (成功, 错误信息, SQLSTATE)"""
        c = self.connect()
        try:
            c._cur.execute(sql)
            return True, "", ""
        except Exception as e:
            return False, str(e).strip().split("\n")[0][:400], str(getattr(e, "pgcode", "") or "")

    def scalar(self, sql: str, default: Any = None) -> Any:
        """取第一行第一列"""
        c = self.connect()
        try:
            c._cur.execute(sql)
            row = c._cur.fetchone()
            return row[0] if row else default
        except Exception:
            return default

    def rows(self, sql: str, limit: int = 200) -> Tuple[List[tuple], str]:
        """取多行，返回 (rows, error)"""
        c = self.connect()
        try:
            c._cur.execute(sql)
            return c._cur.fetchmany(limit), ""
        except Exception as e:
            return [], str(e).strip().split("\n")[0][:300]

    # ---------------- P1-4: L3.5 首调冒烟原语 ----------------

    @staticmethod
    def _full_error(e: Exception) -> str:
        """提取完整错误文本（含 CONTEXT）。

        触发器/函数体是 plpgsql 延迟解析——安装成功、首次调用才炸，
        归因必须靠 CONTEXT 里的 ``PL/pgSQL function <name>()...``。
        """
        msg = str(e).strip()
        ctx = ""
        diag = getattr(e, "diag", None)
        if diag is not None:
            try:
                ctx = diag.context or ""
            except Exception:
                ctx = ""
        text = msg + (f"\nCONTEXT: {ctx}" if ctx else "")
        return text.replace("\n", " | ")[:800]

    def execute_detailed(self, sql: str) -> Tuple[bool, str]:
        """执行单条 SQL；失败时返回完整错误文本（含 CONTEXT，便于归因）。"""
        c = self.connect()
        try:
            c._cur.execute(sql)
            return True, ""
        except Exception as e:
            return False, self._full_error(e)

    def try_dml_rollback(self, statements: List[str]) -> List[Tuple[bool, str]]:
        """事务内依次执行 DML，无论成败最终 ROLLBACK（不留下任何数据）。

        前一条失败后事务已 aborted，后续语句不再执行，用 (False, "<aborted>") 占位。
        返回与入参等长的 [(ok, error)] 列表。
        """
        c = self.connect()
        results: List[Tuple[bool, str]] = []
        prev = c._conn.autocommit
        aborted = False
        try:
            c._conn.autocommit = False
            for st in statements:
                if aborted:
                    results.append((False, "<aborted>"))
                    continue
                try:
                    c._cur.execute(st)
                    results.append((True, ""))
                except Exception as e:
                    results.append((False, self._full_error(e)))
                    aborted = True
            return results
        finally:
            try:
                c._conn.rollback()
            except Exception:
                pass
            c._conn.autocommit = True

    # ---------------- 安装 ----------------

    def install_script(self, sql: str, label: str = "") -> List[ObjectInstall]:
        """
        逐语句安装脚本，返回每个 CREATE 对象的安装结果。

        非 CREATE 语句（INSERT/SET 等）失败时也记录，但标记为 DATA 类型，
        便于区分"结构装不上"和"数据装不上"。
        """
        results: List[ObjectInstall] = []
        for st in split_statements(sql):
            kind = classify(st)
            if kind in ("OTHER", "COMMENT", "GRANT"):
                ok, err, state = self.execute(st)
                if not ok:
                    results.append(ObjectInstall(
                        object_name=label or st[:40], kind=kind,
                        installed=False, error=err, sql_state=state,
                    ))
                continue

            name = extract_object_name(st) or (label or st[:40])
            ok, err, state = self.execute(st)
            results.append(ObjectInstall(
                object_name=name, kind=kind,
                installed=ok, error=err, sql_state=state,
            ))
        return results

    def _query_one(self, sql: str, params: tuple) -> bool:
        c = self.connect()
        try:
            c._cur.execute(sql, params)
            return c._cur.fetchone() is not None
        except Exception:
            return False

    def table_exists(self, table: str) -> bool:
        return self._query_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = lower(%s)",
            (self.schema, table),
        )

    def object_exists(self, kind: str, name: str) -> bool:
        """检查某类对象是否存在于沙箱"""
        kind_upper = kind.upper()
        if kind_upper in ("FUNCTION", "PROCEDURE"):
            extra = " AND p.prokind = 'p'" if kind_upper == "PROCEDURE" else ""
            return self._query_one(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                f"WHERE n.nspname IN (%s, 'pkg_score_manage') AND lower(p.proname) = lower(%s){extra}",
                (self.schema, name),
            )
        if kind_upper == "TABLE":
            return self.table_exists(name)
        if kind_upper == "VIEW":
            return self._query_one(
                "SELECT 1 FROM information_schema.views "
                "WHERE table_schema = %s AND table_name = lower(%s)",
                (self.schema, name),
            )
        if kind_upper == "SEQUENCE":
            return self._query_one(
                "SELECT 1 FROM information_schema.sequences "
                "WHERE sequence_schema = %s AND sequence_name = lower(%s)",
                (self.schema, name),
            )
        if kind_upper == "TRIGGER":
            return self._query_one(
                "SELECT 1 FROM information_schema.triggers "
                "WHERE trigger_schema = %s AND lower(trigger_name) = lower(%s)",
                (self.schema, name),
            )
        return False

    def object_exists_any(self, name: str) -> bool:
        """任意类型对象是否存在（用例未声明 object_kind 时的默认判定）"""
        return any(self.object_exists(k, name)
                   for k in ("FUNCTION", "PROCEDURE", "TABLE", "VIEW", "SEQUENCE"))
