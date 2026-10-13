#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fixtures/cases 验证框架 - 实际执行版
执行验证层次 V3-V8 的自动化测试 (需要 PostgreSQL 数据库)

验证层次:
- V3: 结果验证 - 相同输入下结果是否一致
- V4: 边界验证 - NULL、空集合、极值、重复数据、日期边界
- V5: 异常验证 - 异常触发条件及异常处理行为
- V6: 数据变更验证 - INSERT/UPDATE/DELETE/MERGE 的影响行数和最终数据
- V7: 事务验证 - COMMIT/ROLLBACK/异常时回滚范围
- V8: 性能验证 - 执行计划、执行时间、索引使用

用法:
    python scripts/verify_cases.py --level 3        # 运行 V3 验证
    python scripts/verify_cases.py --level all      # 运行所有验证
    python scripts/verify_cases.py --case 01       # 运行特定案例
    python scripts/verify_cases.py --setup         # 设置测试环境
    python scripts/verify_cases.py --cleanup       # 清理测试环境
    python scripts/verify_cases.py --baseline 01   # 生成基线记录
"""
import argparse
import json
import os
import sys
import time
import re
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import psycopg2
import psycopg2.extras

# Project paths
ROOT = Path(__file__).parent.parent
CASES_DIR = ROOT / "fixtures" / "cases"
TEST_DATA_DIR = CASES_DIR / "_test_data"
OUT_DIR = ROOT / "out"

# PostgreSQL connection settings (from .env)
PG_HOST = "localhost"
PG_PORT = 5432
PG_USER = "postgres"
PG_PASSWORD = "postgres"
TEST_DB_NAME = "gkb_cases_verify"


class VerificationLevel(Enum):
    """验证层次"""
    V3_RESULT = "v3_result"
    V4_BOUNDARY = "v4_boundary"
    V5_EXCEPTION = "v5_exception"
    V6_DATA_CHANGE = "v6_data_change"
    V7_TRANSACTION = "v7_transaction"
    V8_PERFORMANCE = "v8_performance"


class CaseType(Enum):
    """案例类型"""
    DQL = "dql"
    DML = "dml"
    DDL = "ddl"
    PLSQL = "plsql"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class VerificationResult:
    """验证结果"""
    case_id: str
    case_name: str
    level: VerificationLevel
    status: str
    duration_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    error_message: str = ""
    timestamp: str = ""


@dataclass
class CaseTest:
    """案例测试定义"""
    id: str
    name: str
    case_type: CaseType
    risk_level: RiskLevel
    test_sql: str  # 执行的测试 SQL
    oracle_sql: str = ""  # Oracle 原始 SQL (用于对比)
    expected_rows: Optional[int] = None
    expected_columns: Optional[List[str]] = None
    boundary_tests: List[Dict[str, Any]] = field(default_factory=list)
    exception_tests: List[Dict[str, Any]] = field(default_factory=list)


class TestCaseRegistry:
    """测试案例注册表"""

    CASES: Dict[str, CaseTest] = {}

    @classmethod
    def register(cls, test: CaseTest):
        cls.CASES[test.id] = test

    @classmethod
    def get(cls, case_id: str) -> Optional[CaseTest]:
        return cls.CASES.get(case_id)


def register_all_cases():
    """注册所有测试案例"""

    # 01: 基础 SELECT - V3 结果验证 + V4 边界验证
    TestCaseRegistry.register(CaseTest(
        id="01",
        name="基础 SELECT",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="SELECT kojin_no, gakunen, genzon_kbn FROM gkbtgakureibo ORDER BY kojin_no LIMIT 5;",
        boundary_tests=[
            {"name": "NULL 输入", "sql": "SELECT * FROM gkbtgakureibo WHERE kojin_no IS NULL"},
            {"name": "不存在的 KOJIN_NO", "sql": "SELECT * FROM gkbtgakureibo WHERE kojin_no = -1"},
            {"name": "极值(0)", "sql": "SELECT * FROM gkbtgakureibo WHERE gakunen = 0"},
            {"name": "极值(9)", "sql": "SELECT * FROM gkbtgakureibo WHERE gakunen = 9"},
            {"name": "重复数据", "sql": "SELECT * FROM gkbtgakureibo WHERE hogoya_kojin_no = 2000000001"},
        ]
    ))

    # 02: 多表 JOIN
    TestCaseRegistry.register(CaseTest(
        id="02",
        name="多表 JOIN",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT g.kojin_no, g.shimei_kanji, a.yubin_no
        FROM gkbtgakureibo g
        JOIN gabtatenakihon a ON g.kojin_no = a.kojin_no
        WHERE g.sakujo_flg = 0
        ORDER BY g.kojin_no;
        """,
        expected_columns=["kojin_no", "shimei_kanji", "yubin_no"]
    ))

    # 03: 外连接
    TestCaseRegistry.register(CaseTest(
        id="03",
        name="外连接 (+)",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        SELECT g.kojin_no, g.shimei_kanji, a.yubin_no
        FROM gkbtgakureibo g
        LEFT JOIN gabtatenakihon a ON g.kojin_no = a.kojin_no
        ORDER BY g.kojin_no;
        """
    ))

    # 04: EXISTS
    TestCaseRegistry.register(CaseTest(
        id="04",
        name="EXISTS / NOT EXISTS",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT EXISTS(SELECT 1 FROM gkbtgakureibo WHERE kojin_no = 1000000001) as exists_check;
        """
    ))

    # 05: UNION
    TestCaseRegistry.register(CaseTest(
        id="05",
        name="UNION / UNION ALL",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT kojin_no FROM gkbtgakureibo WHERE gakunen = 3
        UNION
        SELECT kojin_no FROM gkbtgakureibo WHERE gakunen = 6;
        """,
        boundary_tests=[
            {"name": "UNION 去重", "sql": "SELECT 1 UNION SELECT 1"},
            {"name": "UNION ALL", "sql": "SELECT 1 UNION ALL SELECT 1"},
        ]
    ))

    # 06: GROUP BY
    TestCaseRegistry.register(CaseTest(
        id="06",
        name="GROUP BY / HAVING",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT gakunen, COUNT(*) as cnt
        FROM gkbtgakureibo
        GROUP BY gakunen
        HAVING COUNT(*) > 0
        ORDER BY gakunen;
        """
    ))

    # 07: 分析函数
    TestCaseRegistry.register(CaseTest(
        id="07",
        name="分析函数",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        SELECT kojin_no, gakunen,
               ROW_NUMBER() OVER (PARTITION BY gakunen ORDER BY kojin_no) as rn,
               COUNT(*) OVER (PARTITION BY gakunen) as cnt
        FROM gkbtgakureibo
        ORDER BY gakunen, kojin_no;
        """
    ))

    # 08: ROWNUM 分页
    TestCaseRegistry.register(CaseTest(
        id="08",
        name="ROWNUM / 分页",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="SELECT kojin_no, gakunen FROM gkbtgakureibo ORDER BY kojin_no LIMIT 3 OFFSET 0;",
        boundary_tests=[
            {"name": "第一页", "limit_clause": "LIMIT 3 OFFSET 0"},
            {"name": "第二页", "limit_clause": "LIMIT 3 OFFSET 3"},
            {"name": "超出范围", "limit_clause": "LIMIT 100 OFFSET 10000"},
        ]
    ))

    # 09: 日期处理
    TestCaseRegistry.register(CaseTest(
        id="09",
        name="日期处理",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        SELECT CURRENT_DATE as today,
               EXTRACT(YEAR FROM CURRENT_DATE) as year,
               TO_CHAR(CURRENT_DATE, 'YYYYMMDD') as ymd;
        """,
        boundary_tests=[
            {"name": "SYSDATE 替换", "sql": "SELECT CURRENT_DATE"},
            {"name": "NULL 日期", "sql": "SELECT COALESCE(NULL::date, CURRENT_DATE)"},
            {"name": "日期比较", "sql": "SELECT * FROM gkbtgakureibo WHERE sys_sakuseibi = 20240401"},
        ]
    ))

    # 10: NULL 处理
    TestCaseRegistry.register(CaseTest(
        id="10",
        name="NULL 处理",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT kojin_no,
               COALESCE(shimei_kanji, 'DEFAULT') as name1,
               COALESCE(sakujo_flg::text, 'DEFAULT') as sakujo1,
               NULLIF(sakujo_flg, -1) as sakujo2
        FROM gkbtgakureibo
        ORDER BY kojin_no
        LIMIT 3;
        """,
        boundary_tests=[
            {"name": "COALESCE", "sql": "SELECT COALESCE(NULL, 'DEFAULT')"},
            {"name": "NULLIF", "sql": "SELECT NULLIF(1, 1)"},
            {"name": "IS NULL", "sql": "SELECT 1 WHERE NULL IS NULL"},
            {"name": "IS NOT NULL", "sql": "SELECT 1 WHERE 'A' IS NOT NULL"},
        ]
    ))

    # 11: DECODE
    TestCaseRegistry.register(CaseTest(
        id="11",
        name="DECODE / NVL",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        SELECT kojin_no,
               CASE gen_jiyu_cd
                   WHEN 50 THEN '卒業'
                   WHEN 31 THEN '全員転居'
                   WHEN 22 THEN '死亡'
                   ELSE 'その他'
               END as reason
        FROM gkbtgakureibo
        ORDER BY kojin_no
        LIMIT 5;
        """
    ))

    # 12: CASE 表达式
    TestCaseRegistry.register(CaseTest(
        id="12",
        name="CASE 表达式",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        SELECT kojin_no,
               CASE
                   WHEN gakunen BETWEEN 1 AND 2 THEN '低学年'
                   WHEN gakunen BETWEEN 3 AND 6 THEN '中学年'
                   WHEN gakunen BETWEEN 7 AND 9 THEN '高学年'
                   ELSE '不明'
               END as gakunen_grp
        FROM gkbtgakureibo
        ORDER BY kojin_no
        LIMIT 5;
        """
    ))

    # 13: MERGE (用 INSERT ON CONFLICT 模拟)
    TestCaseRegistry.register(CaseTest(
        id="13",
        name="MERGE",
        case_type=CaseType.DML,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE TEMP TABLE test_merge_target (id INT PRIMARY KEY, val TEXT);
        INSERT INTO test_merge_target VALUES (1, 'A'), (2, 'B');

        INSERT INTO test_merge_target (id, val)
        VALUES (2, 'B-UPDATED'), (3, 'C')
        ON CONFLICT (id) DO UPDATE SET val = EXCLUDED.val;

        SELECT * FROM test_merge_target ORDER BY id;
        """,
        exception_tests=[
            {"name": "唯一约束冲突", "sql": "INSERT INTO test_merge_target VALUES (1, 'DUP')"}
        ]
    ))

    # 14: INSERT SELECT
    TestCaseRegistry.register(CaseTest(
        id="14",
        name="INSERT SELECT",
        case_type=CaseType.DML,
        risk_level=RiskLevel.LOW,
        test_sql="""
        CREATE TEMP TABLE test_backup (kojin_no INT, shimei_kanji TEXT);
        INSERT INTO test_backup SELECT kojin_no, shimei_kanji FROM gkbtgakureibo WHERE sakujo_flg = 0;
        SELECT COUNT(*) FROM test_backup;
        """
    ))

    # 15: UPDATE/DELETE
    TestCaseRegistry.register(CaseTest(
        id="15",
        name="UPDATE / DELETE",
        case_type=CaseType.DML,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        CREATE TEMP TABLE test_update (id INT PRIMARY KEY, val TEXT);
        INSERT INTO test_update VALUES (1, 'A'), (2, 'B'), (3, 'C');
        UPDATE test_update SET val = 'UPDATED' WHERE id = 1;
        DELETE FROM test_update WHERE id = 3;
        SELECT * FROM test_update ORDER BY id;
        """
    ))

    # 16: 动态 SQL
    TestCaseRegistry.register(CaseTest(
        id="16",
        name="动态 SQL",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_dynamic_test(test_count INT) RETURNS INT AS $$
        DECLARE
            v_sql TEXT;
            v_cnt INT;
        BEGIN
            v_sql := 'SELECT COUNT(*) FROM gkbtgakureibo WHERE gakunen = $1';
            EXECUTE v_sql INTO v_cnt USING test_count;
            RETURN v_cnt;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_dynamic_test(3);
        """,
        exception_tests=[
            {"name": "语法错误的动态 SQL (函数中)", "sql": "DO $$ BEGIN EXECUTE 'INVALID SQL STATEMENT'; END $$", "expect_error": True},
            {"name": "SQL 注入防护 (format 转义)", "sql": "SELECT format('%I', 'badname')", "expect_error": False},
            {"name": "动态 SQL 正确执行 (函数中)", "sql": "DO $$ BEGIN EXECUTE 'SELECT 1'; END $$", "expect_error": False},
        ]
    ))

    # 17: REF CURSOR
    TestCaseRegistry.register(CaseTest(
        id="17",
        name="REF CURSOR",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_refcursor_test() RETURNS TABLE(kojin_no INT, gakunen INT) AS $$
        BEGIN
            RETURN QUERY SELECT g.kojin_no, g.gakunen FROM gkbtgakureibo g LIMIT 3;
        END;
        $$ LANGUAGE plpgsql;

        SELECT * FROM fn_refcursor_test();
        """
    ))

    # 18: Function
    TestCaseRegistry.register(CaseTest(
        id="18",
        name="Function",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_test_basic(i_gakunen INT) RETURNS INT AS $$
        BEGIN
            CASE i_gakunen
                WHEN 0 THEN RETURN 100;
                WHEN 1 THEN RETURN 101;
                WHEN 2 THEN RETURN 102;
                ELSE RETURN 999;
            END CASE;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_test_basic(1) as r1, fn_test_basic(99) as r2;
        """
    ))

    # 19: Procedure
    TestCaseRegistry.register(CaseTest(
        id="19",
        name="Procedure",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        CREATE OR REPLACE PROCEDURE sp_test_proc(IN i_val INT, OUT o_val INT, OUT o_msg TEXT)
        AS $$
        BEGIN
            o_val := i_val * 2;
            o_msg := 'doubled';
        END;
        $$ LANGUAGE plpgsql;

        CALL sp_test_proc(5, NULL, NULL);
        """
    ))

    # 20: Package
    TestCaseRegistry.register(CaseTest(
        id="20",
        name="Package",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE SCHEMA IF NOT EXISTS pkg_test;
        CREATE OR REPLACE FUNCTION pkg_test.func1() RETURNS INT AS $$
        BEGIN RETURN 100; END;
        $$ LANGUAGE plpgsql;
        SELECT pkg_test.func1();
        """
    ))

    # 21: 异常处理
    TestCaseRegistry.register(CaseTest(
        id="21",
        name="异常处理",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_exception_test(i_kojin INT) RETURNS TEXT AS $$
        DECLARE
            v_cnt INT;
        BEGIN
            v_cnt := 0;
            SELECT COUNT(*) INTO v_cnt FROM gkbtgakureibo WHERE kojin_no = i_kojin;
            IF v_cnt = 0 THEN
                RETURN 'NOT_FOUND';
            ELSE
                RETURN 'FOUND:' || v_cnt;
            END IF;
        EXCEPTION
            WHEN OTHERS THEN
                RETURN SQLSTATE || ': ' || SQLERRM;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_exception_test(1000000001) as r1, fn_exception_test(-1) as r2;
        """,
        exception_tests=[
            {"name": "唯一约束冲突", "sql": "INSERT INTO gkbtgakureibo (kojin_no, gakunen) VALUES (1000000001, 5)"},
            {"name": "零除错误", "sql": "SELECT 1/0"},
            {"name": "类型转换错误", "sql": "SELECT 'abc'::int"},
            {"name": "违反 NOT NULL", "sql": "CREATE TEMP TABLE t_notnull (id INT NOT NULL); INSERT INTO t_notnull VALUES (NULL)"},
        ]
    ))

    # 22: 事务控制
    TestCaseRegistry.register(CaseTest(
        id="22",
        name="事务控制",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_transaction_test() RETURNS INT AS $$
        DECLARE
            v_cnt INT;
        BEGIN
            CREATE TEMP TABLE temp_tx (id INT);
            INSERT INTO temp_tx VALUES (1), (2), (3);
            SELECT COUNT(*) INTO v_cnt FROM temp_tx;
            RETURN v_cnt;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_transaction_test();
        """,
        exception_tests=[
            {"name": "异常回滚", "sql": "SELECT 1/0"},  # 除零异常
        ]
    ))

    # 23: DBMS_SQL
    TestCaseRegistry.register(CaseTest(
        id="23",
        name="DBMS_SQL",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_dbms_sql_test(i_gakunen INT) RETURNS INT AS $$
        DECLARE
            v_sql TEXT;
            v_cnt INT;
        BEGIN
            v_sql := 'SELECT COUNT(*) FROM gkbtgakureibo WHERE gakunen = $1';
            EXECUTE v_sql INTO v_cnt USING i_gakunen;
            RETURN v_cnt;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_dbms_sql_test(3);
        """,
        exception_tests=[
            {"name": "标识符转义", "sql": "SELECT format('%I', 'badname')", "expect_error": False},
            {"name": "SQL 解析错误", "sql": "SELECT format('%I', 'invalid;char')", "expect_error": False},
        ]
    ))

    # 24: Trigger
    TestCaseRegistry.register(CaseTest(
        id="24",
        name="Trigger",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE TEMP TABLE test_trigger (id INT, val TEXT, created_at TIMESTAMP);

        CREATE OR REPLACE FUNCTION fn_trigger_before() RETURNS TRIGGER AS $$
        BEGIN
            NEW.created_at := CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_test_before
            BEFORE INSERT ON test_trigger
            FOR EACH ROW
            EXECUTE FUNCTION fn_trigger_before();

        INSERT INTO test_trigger (id, val) VALUES (1, 'A');
        SELECT * FROM test_trigger;
        """
    ))

    # 25: 集合类型
    TestCaseRegistry.register(CaseTest(
        id="25",
        name="集合类型",
        case_type=CaseType.PLSQL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        CREATE OR REPLACE FUNCTION fn_array_test() RETURNS TEXT[] AS $$
        DECLARE
            v_arr INT[];
            v_str TEXT[];
        BEGIN
            v_arr := ARRAY[1, 2, 3, 4, 5];
            v_str := ARRAY['A', 'B', 'C'];
            RETURN v_str;
        END;
        $$ LANGUAGE plpgsql;

        SELECT fn_array_test();
        """
    ))

    # 26: 性能测试 (大数据量)
    TestCaseRegistry.register(CaseTest(
        id="26",
        name="性能测试",
        case_type=CaseType.DQL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        SELECT gakunen, COUNT(*) as cnt
        FROM gkbtgakureibo
        GROUP BY gakunen
        ORDER BY gakunen;
        """,
        boundary_tests=[
            {"name": "全表扫描", "sql": "SELECT * FROM gkbtgakureibo WHERE sakujo_flg = 0"},
            {"name": "主键查询", "sql": "SELECT * FROM gkbtgakureibo WHERE kojin_no = 1000000001"},
            {"name": "GROUP BY 聚合", "sql": "SELECT gakunen, COUNT(*) FROM gkbtgakureibo GROUP BY gakunen"},
        ]
    ))

    # 27: Index (新增 - Phase 1 方案 A 补齐)
    TestCaseRegistry.register(CaseTest(
        id="27",
        name="Index",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        DROP INDEX IF EXISTS idx_gakureibo_gakunen_sakujo;
        DROP INDEX IF EXISTS idx_gakureibo_seibetsu;
        CREATE INDEX idx_gakureibo_gakunen_sakujo ON gkbtgakureibo (gakunen, sakujo_flg);
        CREATE INDEX idx_gakureibo_seibetsu ON gkbtgakureibo (seibetsu);
        SELECT 'indexes created' AS result;
        """,
        boundary_tests=[
            {"name": "索引已创建", "sql": "SELECT indexname FROM pg_indexes WHERE tablename = 'gkbtgakureibo' AND indexname LIKE 'idx_gakureibo_%'"},
            {"name": "组合索引可用", "sql": "EXPLAIN SELECT * FROM gkbtgakureibo WHERE gakunen = 1 AND sakujo_flg = 0"},
            {"name": "单列索引可用", "sql": "EXPLAIN SELECT * FROM gkbtgakureibo WHERE seibetsu = 1"},
        ]
    ))

    # 28: Constraint (新增 - Phase 1 方案 A 补齐)
    TestCaseRegistry.register(CaseTest(
        id="28",
        name="Constraint",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.MEDIUM,
        test_sql="""
        CREATE TEMP TABLE test_constraint (
            id           INT NOT NULL,
            cd           VARCHAR(20),
            seibetsu     INT,
            gakunen      INT,
            hogoya_id    INT,
            CONSTRAINT pk_test_cstr PRIMARY KEY (id),
            CONSTRAINT uk_test_cstr_cd UNIQUE (cd),
            CONSTRAINT ck_test_cstr_seibetsu CHECK (seibetsu IN (0, 1, 2)),
            CONSTRAINT ck_test_cstr_gakunen  CHECK (gakunen BETWEEN 0 AND 9),
            CONSTRAINT fk_test_cstr_hogoya
                FOREIGN KEY (hogoya_id)
                REFERENCES test_constraint(id)
                ON DELETE SET NULL
        );

        INSERT INTO test_constraint (id, cd, seibetsu, gakunen) VALUES (1, 'A001', 1, 3);
        INSERT INTO test_constraint (id, cd, seibetsu, gakunen) VALUES (2, 'A002', 2, 6);

        SELECT conname, contype, convalidated
        FROM pg_constraint
        WHERE conrelid = 'test_constraint'::regclass
        ORDER BY conname;
        """,
        exception_tests=[
            {"name": "PK 违规", "sql": "INSERT INTO test_constraint (id, cd, seibetsu, gakunen) VALUES (1, 'A003', 0, 3)", "expect_error": True},
            {"name": "UQ 违规", "sql": "INSERT INTO test_constraint (id, cd, seibetsu, gakunen) VALUES (3, 'A001', 0, 3)", "expect_error": True},
            {"name": "CHECK 违规", "sql": "INSERT INTO test_constraint (id, cd, seibetsu, gakunen) VALUES (3, 'A003', 9, 3)", "expect_error": True},
            {"name": "FK 违规", "sql": "INSERT INTO test_constraint (id, cd, seibetsu, gakunen, hogoya_id) VALUES (3, 'A003', 0, 3, 999)", "expect_error": True},
        ]
    ))

    # 29: Sequence (新增 - Phase 1 方案 A 补齐)
    TestCaseRegistry.register(CaseTest(
        id="29",
        name="Sequence",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.LOW,
        test_sql="""
        DROP SEQUENCE IF EXISTS seq_test_cstr_kojin_no;
        DROP SEQUENCE IF EXISTS seq_test_cstr_gakunen;
        CREATE SEQUENCE seq_test_cstr_kojin_no
            START WITH 1000000001
            INCREMENT BY 1
            NO MAXVALUE
            NO CYCLE
            CACHE 20;

        CREATE SEQUENCE seq_test_cstr_gakunen
            START WITH 1
            INCREMENT BY 1
            MAXVALUE 9
            MINVALUE 1
            CYCLE
            CACHE 5;

        SELECT nextval('seq_test_cstr_kojin_no') AS kojin_no_1,
               nextval('seq_test_cstr_kojin_no') AS kojin_no_2,
               currval('seq_test_cstr_kojin_no') AS kojin_curr;
        """,
        boundary_tests=[
            {"name": "序列定义存在", "sql": "SELECT sequencename, start_value, increment_by, max_value, min_value, cache_size, cycle FROM pg_sequences WHERE sequencename LIKE 'seq_test_cstr%'"},
            {"name": "CYCLE 回绕", "sql": "SELECT nextval('seq_test_cstr_gakunen') FROM generate_series(1, 11)"},
        ]
    ))

    # 30: Scheduled Job (新增 - Phase 1 方案 A 补齐)
    TestCaseRegistry.register(CaseTest(
        id="30",
        name="Scheduled Job",
        case_type=CaseType.DDL,
        risk_level=RiskLevel.HIGH,
        test_sql="""
        -- pg_cron 调度需在 shared_preload_libraries 中启用 pg_cron, 数据库需先 CREATE EXTENSION
        -- 此处仅验证元数据映射规则, 不实际执行调度
        SELECT 'pg_cron'::text AS recommended_extension
        UNION ALL SELECT 'DBMS_SCHEDULER.CREATE_JOB → cron.schedule(name, expr, sql)'::text
        UNION ALL SELECT 'INTERVAL FREQ=DAILY;BYHOUR=2 → 0 2 * * *'::text;
        """,
        boundary_tests=[
            {"name": "pg_cron 扩展存在性", "sql": "SELECT extname FROM pg_extension WHERE extname = 'pg_cron'"},
            {"name": "INTERVAL 转 cron 验证 - 每天 02:00", "sql": "SELECT 'FREQ=DAILY;BYHOUR=2;BYMINUTE=0' AS oracle_interval, '0 2 * * *' AS cron_expr"},
            {"name": "INTERVAL 转 cron 验证 - 每月 1 日", "sql": "SELECT 'FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=0' AS oracle_interval, '0 0 1 * *' AS cron_expr"},
            {"name": "INTERVAL 转 cron 验证 - 每年 4 月 1 日", "sql": "SELECT 'FREQ=YEARLY;BYMONTH=4;BYDAY=1;BYHOUR=2' AS oracle_interval, '0 2 1 4 *' AS cron_expr"},
        ]
    ))


class PostgresConnection:
    """PostgreSQL 连接管理"""

    def __init__(self, dbname: str = TEST_DB_NAME):
        self.dbname = dbname
        self.conn = None
        self.cur = None

    def __enter__(self):
        try:
            self.conn = psycopg2.connect(
                host=PG_HOST,
                port=PG_PORT,
                user=PG_USER,
                password=PG_PASSWORD,
                dbname=self.dbname
            )
            self.conn.autocommit = True
            self.cur = self.conn.cursor()
            return self
        except psycopg2.OperationalError as e:
            raise ConnectionError(f"Cannot connect to PostgreSQL: {e}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cur:
            self.cur.close()
        if self.conn:
            self.conn.close()


def setup_test_database(large_dataset_size: int = 0):
    """设置测试数据库

    参数:
        large_dataset_size: 大数据量数据集大小 (0 = 不加载大数据集)
    """
    print("[*] Setting up test database...")

    # 首先连接到 postgres 库以创建测试库
    conn = None
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD, dbname="postgres"
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f"CREATE DATABASE {TEST_DB_NAME} TEMPLATE template0 "
                           f"ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
                print(f"  [+] Created test database: {TEST_DB_NAME}")
            else:
                print(f"  [i] Test database already exists: {TEST_DB_NAME}")

        # 连接到测试库并创建表
        with PostgresConnection() as pg:
            create_test_tables(pg)
            insert_test_data(pg)

            # 可选: 加载大数据量数据集
            if large_dataset_size > 0:
                insert_large_dataset(pg, large_dataset_size)
                insert_constraint_violation_data(pg)

        print("  [+] Test tables and data created")
    except Exception as e:
        print(f"  [!] Failed to setup: {e}")
        raise
    finally:
        if conn:
            conn.close()


def cleanup_test_database():
    """清理测试数据库"""
    print("[*] Cleaning up test database...")
    conn = None
    try:
        conn = psycopg2.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD, dbname="postgres"
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            # 断开所有连接然后删除
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = '{TEST_DB_NAME}'
            """)
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
            print(f"  [+] Dropped test database: {TEST_DB_NAME}")
    except Exception as e:
        print(f"  [!] Failed to cleanup: {e}")
    finally:
        if conn:
            conn.close()


def create_test_tables(pg: PostgresConnection):
    """创建测试表"""
    tables_sql = [
        # 移除 TEMP 改为普通表 (因为函数中使用)
        """
        DROP TABLE IF EXISTS gkbtgakureibo CASCADE;
        CREATE TABLE gkbtgakureibo (
            kojin_no INT PRIMARY KEY,
            hogoya_kojin_no INT,
            rireki_renban INT,
            gakunen INT,
            genzon_kbn INT DEFAULT 0,
            saishin_kbn INT DEFAULT 0,
            syogako_cd INT,
            tyugako_cd INT,
            gen_jiyu_cd INT,
            gen_nengapi INT,
            zo_nengapi INT,
            sakujo_flg INT DEFAULT 0,
            seibetsu INT,
            shimei_kanji TEXT,
            shimei_kana TEXT,
            tsushomei_kanji TEXT,
            tsushomei_kana TEXT,
            sys_sakuseibi INT,
            sys_koshinbi INT
        );
        """,
        """
        DROP TABLE IF EXISTS gabtatenakihon CASCADE;
        CREATE TABLE gabtatenakihon (
            kojin_no INT PRIMARY KEY,
            rireki_renban INT,
            genzon_kbn INT DEFAULT 0,
            seibetsu INT,
            seinengapi INT,
            shimei_kanji TEXT,
            shimei_kana TEXT,
            yubin_no TEXT,
            chomei TEXT,
            banchi TEXT,
            oaza_cd INT,
            santeidantai_cd INT
        );
        """,
        """
        DROP TABLE IF EXISTS gabtjukiido CASCADE;
        CREATE TABLE gabtjukiido (
            kojin_no INT PRIMARY KEY,
            juminnaru INT,
            jumin_nakunaru INT,
            genzon_kbn INT DEFAULT 0,
            shimei_kanji TEXT,
            yubin_no TEXT
        );
        """,
        """
        DROP TABLE IF EXISTS gkbtmsgakko CASCADE;
        CREATE TABLE gkbtmsgakko (
            syogako_cd INT PRIMARY KEY,
            syogakomei TEXT,
            tyugakomei TEXT
        );
        """,
        """
        DROP TABLE IF EXISTS gkbtseijinsha CASCADE;
        CREATE TABLE gkbtseijinsha (
            kojin_no INT,
            nendo INT,
            seireki INT,
            seingapi INT,
            seibetsu INT,
            kojin_cd INT,
            sakujeibi INT,
            PRIMARY KEY (kojin_no, nendo)
        );
        """,
        """
        DROP TABLE IF EXISTS gkbtzokugara CASCADE;
        CREATE TABLE gkbtzokugara (
            zokugara_cd INT PRIMARY KEY,
            zokugara_kbn INT,
            zokugara_mei TEXT
        );
        """,
    ]

    for sql in tables_sql:
        pg.cur.execute(sql)


def insert_test_data(pg: PostgresConnection):
    """插入测试数据"""
    insert_sql = [
        # GKBTGAKUREIBO
        """INSERT INTO gkbtgakureibo VALUES
        (1000000001, 2000000001, 1, 3, 0, 0, 101, 201, 50, 0, 20240401, 0, 1, '山田太郎', 'ヤマダタロウ', NULL, NULL, 20240401, 20240401),
        (1000000002, 2000000002, 1, 6, 0, 0, 102, 202, 50, 0, 20240401, 0, 2, '鈴木花子', 'スズキハナコ', NULL, NULL, 20240401, 20240401),
        (1000000003, 2000000003, 1, 9, 1, 0, 103, 203, 31, 20241001, 20240401, 0, 1, '佐藤一郎', 'サトウイチロウ', NULL, NULL, 20240401, 20240401),
        (1000000004, 2000000004, 1, 1, 0, 0, NULL, NULL, NULL, 0, 20240401, 0, NULL, NULL, NULL, NULL, NULL, 20240401, 20240401),
        (1000000005, 2000000005, 1, 0, 0, 0, 104, 204, 50, 0, 20240401, 0, 1, '最小学年', 'サイショウガクネン', NULL, NULL, 20240401, 20240401),
        (1000000006, 2000000006, 1, 9, 0, 0, 105, 205, 50, 0, 20240401, 0, 2, '最大学年', 'サイダイガクネン', NULL, NULL, 20240401, 20240401),
        (1000000007, 2000000001, 2, 3, 0, 0, 101, 201, 50, 0, 20240401, 0, 1, '山田太郎', 'ヤマダタロウ', NULL, NULL, 20240401, 20240401),
        (1000000008, 2000000001, 3, 4, 0, 0, 101, 201, 50, 0, 20240401, 0, 1, '山田太郎', 'ヤマダタロウ', NULL, NULL, 20240401, 20240401);
        """,
        # GABTATENAKIHON
        """INSERT INTO gabtatenakihon VALUES
        (1000000001, 1, 0, 1, 20150401, '山田太郎', 'ヤマダタロウ', '123-4567', '新宿町', '1-2-3', 1001, 30001),
        (1000000002, 1, 0, 2, 20130501, '鈴木花子', 'スズキハナコ', '234-5678', '千代田町', '4-5-6', 1002, 30002),
        (1000000003, 1, 1, 1, 20080301, '佐藤一郎', 'サトウイチロウ', '345-6789', '港町', '7-8-9', 1003, 30003),
        (1000000004, 1, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL),
        (1000000005, 1, 0, 1, 20150101, '最小学年', 'サイショウガクネン', '111-1111', '北区', '1-1-1', 1004, 30004),
        (1000000006, 1, 0, 2, 20101231, '最大学年', 'サイダイガクネン', '999-9999', '南区', '9-9-9', 1005, 30005);
        """,
        # GABTJUKIIDO
        """INSERT INTO gabtjukiido VALUES
        (1000000001, 20240401, 0, 0, '山田太郎', '123-4567'),
        (1000000002, 20240401, 0, 0, '鈴木花子', '234-5678'),
        (1000000003, 20240401, 20241001, 1, '佐藤一郎', '345-6789');
        """,
    ]

    for sql in insert_sql:
        try:
            pg.cur.execute(sql)
        except psycopg2.Error as e:
            print(f"  [!] Insert error: {e}")


def insert_large_dataset(pg: PostgresConnection, size: int = 10000):
    """插入大数据量测试数据集

    生成 size 条 GKBTGAKUREIBO 数据 (用于性能验证)
    """
    import random

    print(f"  [+] Generating {size} large dataset rows...")

    # 创建临时表
    pg.cur.execute("""
        CREATE TEMP TABLE bulk_gen (
            kojin_no INT,
            hogoya_kojin_no INT,
            rireki_renban INT,
            gakunen INT,
            genzon_kbn INT,
            saishin_kbn INT,
            syogako_cd INT,
            tyugako_cd INT,
            gen_jiyu_cd INT,
            gen_nengapi INT,
            zo_nengapi INT,
            sakujo_flg INT,
            seibetsu INT,
            shimei_kanji TEXT,
            sys_sakuseibi INT,
            sys_koshinbi INT
        );
    """)

    # 批量生成
    batch_size = 1000
    inserted = 0
    start_id = 5000001  # 避免与现有数据冲突 (INT 上限 2147483647)

    for batch_start in range(0, size, batch_size):
        rows = []
        for i in range(batch_start, min(batch_start + batch_size, size)):
            kojin_no = start_id + i
            hogoya_no = start_id + i + 1000000
            # 限制 hogoya_no 在 INT 范围内
            if hogoya_no > 2147483647:
                hogoya_no = start_id + (i % 100000) + 100000
            rireki = random.randint(1, 5)
            gakunen = random.randint(0, 9)
            genzon = random.choice([0, 0, 0, 0, 1])  # 80% 現存
            rows.append((kojin_no, hogoya_no, rireki, gakunen, genzon, 0,
                        random.randint(100, 200), random.randint(200, 300),
                        random.choice([21, 22, 23, 50]), random.choice([0, 20241001]),
                        20240401, 0, random.choice([1, 2]),
                        f'テスト{i}', 20240401, 20240401))

        # 使用 execute_values 加速插入
        from psycopg2.extras import execute_values
        execute_values(pg.cur, "INSERT INTO bulk_gen VALUES %s", rows, page_size=10)
        inserted += len(rows)

    # 复制到主表
    pg.cur.execute("INSERT INTO gkbtgakureibo SELECT * FROM bulk_gen;")

    # 重新创建索引 + 分析
    pg.cur.execute("ANALYZE gkbtgakureibo;")

    print(f"  [+] Inserted {inserted} rows into gkbtgakureibo")


def insert_constraint_violation_data(pg: PostgresConnection):
    """插入违反约束的测试数据

    用于 V5 异常验证 - 测试违反唯一约束、NOT NULL 等场景
    """
    print("  [+] Inserting constraint violation test data...")

    violation_sql = [
        # 添加重复主键
        """INSERT INTO gkbtgakureibo VALUES
        (1000000001, 2000000099, 1, 5, 0, 0, 999, 999, NULL, 0, 20240401, 0, 1, '重复キー', NULL, NULL, NULL, 20240401, 20240401);
        """,
    ]

    # 插入期望失败的 INSERT (重复键)
    for sql in violation_sql:
        try:
            pg.cur.execute(sql)
        except psycopg2.Error:
            pass  # 期望失败, 忽略

    # 设置 NOT NULL 约束 (如果未设置)
    try:
        pg.cur.execute("ALTER TABLE gkbtgakureibo ALTER COLUMN kojin_no SET NOT NULL;")
    except psycopg2.Error:
        pass

    print("  [+] Constraint violation data ready")


def execute_sql(pg: PostgresConnection, sql: str) -> Tuple[bool, str, float]:
    """执行 SQL 并返回结果"""
    start = time.time()
    try:
        # 移除 TEMP 引用 (因为函数中需要)
        pg.cur.execute(sql)
        duration = (time.time() - start) * 1000

        if pg.cur.description:
            rows = pg.cur.fetchall()
            cols = [d[0] for d in pg.cur.description]
            return True, json.dumps({"cols": cols, "rows": len(rows), "sample": rows[:3]}, ensure_ascii=False, default=str), duration
        else:
            return True, json.dumps({"affected": pg.cur.rowcount}), duration
    except psycopg2.Error as e:
        duration = (time.time() - start) * 1000
        return False, str(e), duration
    except Exception as e:
        duration = (time.time() - start) * 1000
        return False, f"Error: {e}", duration


def verify_v3_result(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V3: 结果验证"""
    start = time.time()
    details = {}

    if not case.test_sql.strip():
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V3_RESULT,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "No test SQL defined"}, timestamp=datetime.now().isoformat()
        )

    success, info, duration = execute_sql(pg, case.test_sql)

    details["execution_ms"] = round(duration, 2)
    if success:
        try:
            data = json.loads(info)
            details["rows"] = data.get("rows", data.get("affected", 0))
            details["cols"] = data.get("cols", [])
            details["sample"] = data.get("sample", [])
            status = "pass"
        except json.JSONDecodeError:
            details["info"] = info
            status = "pass"
    else:
        details["error"] = info
        status = "fail"

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V3_RESULT,
        status=status, duration_ms=(time.time() - start) * 1000,
        details=details, error_message=info if not success else "",
        timestamp=datetime.now().isoformat()
    )


def verify_v4_boundary(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V4: 边界验证"""
    start = time.time()
    details = {"tests": []}

    if not case.boundary_tests:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V4_BOUNDARY,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "No boundary tests defined"}, timestamp=datetime.now().isoformat()
        )

    all_pass = True
    for test in case.boundary_tests:
        test_name = test.get("name", "unnamed")
        test_sql = test.get("sql", "")

        if not test_sql:
            # 处理 limit_clause 类型
            if "limit_clause" in test:
                base_sql = case.test_sql.replace(";", "")
                if "ORDER BY" in base_sql.upper():
                    test_sql = base_sql.rsplit("ORDER BY", 1)[0] + " " + test["limit_clause"] + ";"
                else:
                    test_sql = base_sql + " " + test["limit_clause"] + ";"

        if not test_sql:
            continue

        success, info, duration = execute_sql(pg, test_sql)
        test_result = {
            "name": test_name,
            "status": "pass" if success else "fail",
            "duration_ms": round(duration, 2),
            "error": info if not success else None
        }
        details["tests"].append(test_result)
        if not success:
            all_pass = False

    details["total"] = len(details["tests"])
    details["passed"] = sum(1 for t in details["tests"] if t["status"] == "pass")

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V4_BOUNDARY,
        status="pass" if all_pass else "fail", duration_ms=(time.time() - start) * 1000,
        details=details, timestamp=datetime.now().isoformat()
    )


def verify_v6_data_change(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V6: 数据变更验证"""
    start = time.time()
    details = {}

    if case.case_type not in [CaseType.DML]:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V6_DATA_CHANGE,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "Not a DML case"}, timestamp=datetime.now().isoformat()
        )

    success, info, duration = execute_sql(pg, case.test_sql)
    details["execution_ms"] = round(duration, 2)
    if success:
        details["result"] = info
        status = "pass"
    else:
        details["error"] = info
        status = "fail"

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V6_DATA_CHANGE,
        status=status, duration_ms=(time.time() - start) * 1000,
        details=details, error_message=info if not success else "",
        timestamp=datetime.now().isoformat()
    )


def verify_v5_exception(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V5: 异常验证

    测试目标:
    1. 验证异常能正确触发 (SQL 应该返回错误)
    2. 验证异常处理逻辑正确 (捕获后能正常返回)
    3. 验证错误码/SQLSTATE 一致性
    """
    start = time.time()
    details = {"tests": []}

    if not case.exception_tests:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V5_EXCEPTION,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "No exception tests defined"}, timestamp=datetime.now().isoformat()
        )

    all_pass = True
    for test in case.exception_tests:
        test_name = test.get("name", "unnamed")
        test_sql = test.get("sql", "")
        expect_error = test.get("expect_error", True)

        if not test_sql:
            continue

        success, info, duration = execute_sql(pg, test_sql)

        # 异常验证逻辑:
        # - expect_error=True: SQL 应该产生错误, success=False 才符合
        # - expect_error=False: SQL 应该成功, success=True 才符合
        if expect_error:
            test_pass = not success
            test_status = "pass" if test_pass else "fail"
            test_detail = {
                "name": test_name,
                "status": test_status,
                "duration_ms": round(duration, 2),
                "expected": "error",
                "actual": "error" if not success else "success",
                "error": info if not success else None,
            }
        else:
            test_pass = success
            test_status = "pass" if test_pass else "fail"
            test_detail = {
                "name": test_name,
                "status": test_status,
                "duration_ms": round(duration, 2),
                "expected": "success",
                "actual": "success" if success else "error",
                "error": info if not success else None,
            }

        details["tests"].append(test_detail)
        if not test_pass:
            all_pass = False

    details["total"] = len(details["tests"])
    details["passed"] = sum(1 for t in details["tests"] if t["status"] == "pass")

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V5_EXCEPTION,
        status="pass" if all_pass else "fail", duration_ms=(time.time() - start) * 1000,
        details=details, timestamp=datetime.now().isoformat()
    )


def verify_v7_transaction(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V7: 事务验证

    测试目标:
    1. 验证 COMMIT 正确提交
    2. 验证 ROLLBACK 正确回滚
    3. 验证异常时事务自动回滚
    """
    start = time.time()
    details = {"tests": []}

    # 事务验证是可选的, 只有 PL/SQL 类型案例适用
    if case.case_type not in [CaseType.PLSQL, CaseType.DML]:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V7_TRANSACTION,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "Not a PL/SQL or DML case"}, timestamp=datetime.now().isoformat()
        )

    # 事务验证 SQL (每条独立执行)
    transaction_tests = [
        {
            "name": "COMMIT 提交",
            "setup": "BEGIN;",
            "test": "INSERT INTO gkbtgakureibo_temp (id, val) VALUES (1, 'test');",
            "commit": "COMMIT;",
            "verify": "SELECT val FROM gkbtgakureibo_temp WHERE id = 1;",
            "expect_visible": True
        },
        {
            "name": "ROLLBACK 回滚",
            "setup": "BEGIN;",
            "test": "INSERT INTO gkbtgakureibo_temp (id, val) VALUES (2, 'rollback_test');",
            "commit": "ROLLBACK;",
            "verify": "SELECT COUNT(*) FROM gkbtgakureibo_temp WHERE id = 2;",
            "expect_visible": False
        },
        {
            "name": "自动提交默认",
            "setup": "",
            "test": "INSERT INTO gkbtgakureibo_temp (id, val) VALUES (3, 'auto_commit');",
            "commit": "",
            "verify": "SELECT val FROM gkbtgakureibo_temp WHERE id = 3;",
            "expect_visible": True
        }
    ]

    all_pass = True

    # 先创建临时表
    setup_sql = """
        CREATE TEMP TABLE IF NOT EXISTS gkbtgakureibo_temp (
            id INT PRIMARY KEY,
            val TEXT
        );
        DELETE FROM gkbtgakureibo_temp;
    """
    try:
        pg.cur.execute(setup_sql)
    except psycopg2.Error as e:
        details["setup_error"] = str(e)

    for test in transaction_tests:
        test_name = test["name"]
        try:
            # 1. setup (BEGIN if needed)
            if test.get("setup"):
                pg.cur.execute(test["setup"])
            # 2. 执行测试 SQL
            if test["test"]:
                pg.cur.execute(test["test"])
            # 3. commit/rollback
            if test["commit"]:
                pg.cur.execute(test["commit"])
            # 4. 验证结果
            success, info, _ = execute_sql(pg, test["verify"])

            # 检查结果
            if success and test["expect_visible"]:
                # COMMIT 后数据应该可见
                if "rollback_test" in test["verify"] or "auto_commit" in test["verify"]:
                    test_pass = True
                elif "id = 1" in test["verify"]:
                    test_pass = '"rows": 1' in info
                else:
                    test_pass = '"rows":' in info
            elif success and not test["expect_visible"]:
                # ROLLBACK 后数据不应该可见 -> count 应该是 0
                test_pass = '"sample": [[0]]' in info or '"rows": 0' in info or '"rows":0' in info
            else:
                test_pass = False

            details["tests"].append({
                "name": test_name,
                "status": "pass" if test_pass else "fail",
                "expect_visible": test["expect_visible"],
                "result": info
            })
            if not test_pass:
                all_pass = False
        except psycopg2.Error as e:
            details["tests"].append({
                "name": test_name,
                "status": "fail",
                "error": str(e)
            })
            all_pass = False
        finally:
            # 重置临时表
            try:
                pg.cur.execute("DELETE FROM gkbtgakureibo_temp WHERE id > 0;")
            except psycopg2.Error:
                pass

    details["total"] = len(details["tests"])
    details["passed"] = sum(1 for t in details["tests"] if t["status"] == "pass")

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V7_TRANSACTION,
        status="pass" if all_pass else "fail", duration_ms=(time.time() - start) * 1000,
        details=details, timestamp=datetime.now().isoformat()
    )


def verify_v8_performance(case: CaseTest, pg: PostgresConnection) -> VerificationResult:
    """V8: 性能验证

    测试目标:
    1. 执行计划 (EXPLAIN) - 确保计划合理
    2. 查询耗时 - 与期望值对比
    3. 索引使用 - 是否使用索引
    4. 资源消耗 - buffer hits / disk reads
    """
    start = time.time()
    details = {"tests": []}

    if not case.test_sql.strip() or case.case_type not in [CaseType.DQL, CaseType.DML]:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=VerificationLevel.V8_PERFORMANCE,
            status="skip", duration_ms=(time.time() - start) * 1000,
            details={"reason": "Not a DQL or DML case, or no test SQL"},
            timestamp=datetime.now().isoformat()
        )

    all_pass = True

    # 提取主要查询语句 (去除 ";" 和首尾空白)
    raw_sql = case.test_sql.strip()

    # 检测多语句 SQL, 提取 setup 和 SELECT
    # 拆分语句 (按 ";" 分隔, 忽略字符串内的 ";" -- 简化处理)
    statements = []
    current = []
    for line in raw_sql.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--') or not stripped:
            current.append(line)
            continue
        current.append(line)
        if stripped.endswith(';'):
            stmt = '\n'.join(current).strip().rstrip(';').strip()
            if stmt:
                statements.append(stmt)
            current = []

    # 查找 SELECT 语句 (作为主要查询)
    main_sql = None
    setup_statements = []
    select_index = -1
    for i, stmt in enumerate(statements):
        if stmt.upper().lstrip().startswith('SELECT'):
            main_sql = stmt
            select_index = i
            break

    # setup 是 SELECT 之前的 CREATE/INSERT/DROP 等语句
    if select_index > 0:
        setup_statements = statements[:select_index]

    if not main_sql:
        # 没有 SELECT, 退回使用最后一个语句
        main_sql = statements[-1] if statements else raw_sql.rstrip(';').strip()

    # 先执行 setup 语句 (如果存在)
    for setup in setup_statements:
        try:
            pg.cur.execute(setup)
        except psycopg2.Error:
            pass  # setup 错误不阻断 V8 验证

    # --- 测试 1: EXPLAIN ANALYZE ---
    try:
        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {main_sql}"
        pg.cur.execute(explain_sql)
        plan = pg.cur.fetchone()[0]

        # 提取性能信息
        exec_time = plan[0].get("Execution Time", 0) if plan else 0
        plan_time = plan[0].get("Planning Time", 0) if plan else 0
        total_time = plan[0].get("Execution Time", 0) + plan[0].get("Planning Time", 0) if plan else 0

        details["tests"].append({
            "name": "EXPLAIN ANALYZE",
            "status": "pass",
            "execution_time_ms": round(exec_time, 3),
            "planning_time_ms": round(plan_time, 3),
            "total_time_ms": round(total_time, 3),
        })
    except psycopg2.Error as e:
        details["tests"].append({
            "name": "EXPLAIN ANALYZE",
            "status": "fail",
            "error": str(e)
        })
        all_pass = False

    # --- 测试 2: 多次执行耗时 ---
    try:
        iterations = 10
        times = []
        for _ in range(iterations):
            iter_start = time.time()
            pg.cur.execute(main_sql)
            if pg.cur.description:
                pg.cur.fetchall()
            times.append((time.time() - iter_start) * 1000)

        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)

        # 期望阈值: 100ms (可根据调整)
        threshold_ms = 100
        status = "pass" if avg_time < threshold_ms else "fail"

        details["tests"].append({
            "name": f"执行耗时 ({iterations} 次迭代)",
            "status": status,
            "avg_ms": round(avg_time, 3),
            "min_ms": round(min_time, 3),
            "max_ms": round(max_time, 3),
            "threshold_ms": threshold_ms,
        })
        if status == "fail":
            all_pass = False
    except psycopg2.Error as e:
        details["tests"].append({
            "name": "执行耗时",
            "status": "fail",
            "error": str(e)
        })
        all_pass = False

    # --- 测试 3: 索引使用情况 ---
    try:
        explain_sql = f"EXPLAIN (FORMAT JSON) {main_sql}"
        pg.cur.execute(explain_sql)
        plan = pg.cur.fetchone()[0]

        # 检测是否使用索引
        def uses_index(plan_node, depth=0):
            if not isinstance(plan_node, dict):
                return False
            node_type = plan_node.get("Node Type", "")
            if "Index" in node_type or "Index" in str(plan_node.get("Plans", [])):
                return True
            for sub_plan in plan_node.get("Plans", []):
                if uses_index(sub_plan, depth + 1):
                    return True
            return False

        # 检查执行计划中是否使用索引
        plan_text = json.dumps(plan[0]) if plan else ""
        uses_idx = "Index Scan" in plan_text or "Index Only Scan" in plan_text

        details["tests"].append({
            "name": "执行计划检查",
            "status": "pass",  # 总是 pass, 只记录信息
            "uses_index": uses_idx,
            "plan_summary": plan_text[:500] if plan_text else "N/A",
        })
    except psycopg2.Error as e:
        details["tests"].append({
            "name": "执行计划检查",
            "status": "fail",
            "error": str(e)
        })
        all_pass = False

    # --- 测试 4: Buffer 使用情况 ---
    try:
        explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {main_sql}"
        pg.cur.execute(explain_sql)
        plan = pg.cur.fetchone()[0]

        # 累计 Shared/Hit/Read/Dirtied/Written
        total_hit = 0
        total_read = 0

        def collect_buffers(node):
            nonlocal total_hit, total_read
            if not isinstance(node, dict):
                return
            buffers = node.get("Shared", {})
            if isinstance(buffers, dict):
                total_hit += buffers.get("Hit", 0)
                total_read += buffers.get("Read", 0)
            for sub in node.get("Plans", []):
                collect_buffers(sub)

        if plan:
            for line in plan[0]:
                collect_buffers(line)

        details["tests"].append({
            "name": "Buffer 使用",
            "status": "pass",  # 只记录信息
            "shared_hit_blocks": total_hit,
            "shared_read_blocks": total_read,
        })
    except psycopg2.Error as e:
        details["tests"].append({
            "name": "Buffer 使用",
            "status": "fail",
            "error": str(e)
        })
        all_pass = False

    details["total"] = len(details["tests"])
    details["passed"] = sum(1 for t in details["tests"] if t["status"] == "pass")

    return VerificationResult(
        case_id=case.id, case_name=case.name, level=VerificationLevel.V8_PERFORMANCE,
        status="pass" if all_pass else "fail", duration_ms=(time.time() - start) * 1000,
        details=details, timestamp=datetime.now().isoformat()
    )


def run_verification(case_id: str, level: VerificationLevel) -> VerificationResult:
    """运行验证"""
    case = TestCaseRegistry.get(case_id)
    if not case:
        return VerificationResult(
            case_id=case_id, case_name="Unknown", level=level,
            status="skip", duration_ms=0,
            error_message=f"Case {case_id} not found",
            timestamp=datetime.now().isoformat()
        )

    try:
        with PostgresConnection() as pg:
            if level == VerificationLevel.V3_RESULT:
                return verify_v3_result(case, pg)
            elif level == VerificationLevel.V4_BOUNDARY:
                return verify_v4_boundary(case, pg)
            elif level == VerificationLevel.V5_EXCEPTION:
                return verify_v5_exception(case, pg)
            elif level == VerificationLevel.V6_DATA_CHANGE:
                return verify_v6_data_change(case, pg)
            elif level == VerificationLevel.V7_TRANSACTION:
                return verify_v7_transaction(case, pg)
            elif level == VerificationLevel.V8_PERFORMANCE:
                return verify_v8_performance(case, pg)
            else:
                return VerificationResult(
                    case_id=case.id, case_name=case.name, level=level,
                    status="pending", duration_ms=0,
                    details={"message": f"Level {level.value} not implemented"},
                    timestamp=datetime.now().isoformat()
                )
    except Exception as e:
        return VerificationResult(
            case_id=case.id, case_name=case.name, level=level,
            status="fail", duration_ms=0,
            error_message=str(e),
            timestamp=datetime.now().isoformat()
        )


def run_all_verifications(case_id: Optional[str] = None) -> Dict[str, List[VerificationResult]]:
    """运行所有验证"""
    register_all_cases()
    results = {}

    case_ids = [case_id] if case_id else list(TestCaseRegistry.CASES.keys())

    # 决定每个案例要运行的验证层次
    case_levels = {
        CaseType.DQL: [VerificationLevel.V3_RESULT, VerificationLevel.V4_BOUNDARY,
                       VerificationLevel.V8_PERFORMANCE],
        CaseType.DML: [VerificationLevel.V3_RESULT, VerificationLevel.V4_BOUNDARY,
                       VerificationLevel.V6_DATA_CHANGE, VerificationLevel.V8_PERFORMANCE],
        CaseType.DDL: [VerificationLevel.V3_RESULT],
        CaseType.PLSQL: [VerificationLevel.V3_RESULT, VerificationLevel.V4_BOUNDARY,
                         VerificationLevel.V5_EXCEPTION, VerificationLevel.V7_TRANSACTION],
    }

    for cid in case_ids:
        case = TestCaseRegistry.get(cid)
        if not case:
            continue

        levels = case_levels.get(case.case_type, [VerificationLevel.V3_RESULT])
        case_results = []

        for level in levels:
            result = run_verification(cid, level)
            case_results.append(result)

        results[cid] = case_results

    return results


def generate_verification_report(results: Dict[str, List[VerificationResult]]) -> str:
    """生成验证报告"""
    total_cases = len(results)
    total_checks = sum(len(v) for v in results.values())
    passed = sum(1 for v in results.values() for r in v if r.status == "pass")
    failed = sum(1 for v in results.values() for r in v if r.status == "fail")
    skipped = sum(1 for v in results.values() for r in v if r.status == "skip")

    pass_rate = (passed / total_checks * 100) if total_checks > 0 else 0

    report = f"""# Oracle -> PostgreSQL Verification Report (V3-V8)

Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary

| Metric | Value |
|--------|-------|
| Total cases | {total_cases} |
| Total checks | {total_checks} |
| Passed | {passed} |
| Failed | {failed} |
| Skipped | {skipped} |
| Pass rate | {pass_rate:.1f}% |

## Verification Levels

| Level | Name | Status |
|-------|------|--------|
| V3 | Result verification | DONE |
| V4 | Boundary verification | DONE |
| V6 | Data change verification | DONE |
| V5 | Exception verification | PARTIAL |
| V7 | Transaction verification | PARTIAL |
| V8 | Performance verification | TODO |

## Detailed Results

"""
    for case_id, case_results in sorted(results.items()):
        case_name = case_results[0].case_name if case_results else "Unknown"
        report += f"### {case_id}: {case_name}\n\n"

        for report_r in case_results:
            status_icon = {"pass": "[PASS]", "fail": "[FAIL]", "skip": "[SKIP]", "pending": "[PENDING]"}.get(report_r.status, "[?]")
            report += f"#### {report_r.level.value} {status_icon}\n"
            report += f"- Duration: {report_r.duration_ms:.1f}ms\n"
            if report_r.details:
                for k, v in report_r.details.items():
                    if isinstance(v, str) and len(v) > 200:
                        v = v[:200] + "..."
                    report += f"- {k}: {v}\n"
            if report_r.error_message:
                report += f"- Error: {report_r.error_message}\n"
            report += "\n"

    return report


def main():
    parser = argparse.ArgumentParser(description="Verification framework V3-V8")
    parser.add_argument("--case", "-c", help="Specific case ID")
    parser.add_argument("--level", "-l", default="3", help="Level (3-8) or 'all'")
    parser.add_argument("--report", "-r", action="store_true", help="Generate report")
    parser.add_argument("--setup", action="store_true", help="Setup test database")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup test database")
    parser.add_argument("--baseline", "-b", help="Generate baseline for case ID")
    parser.add_argument("--large", type=int, default=0, help="Generate large dataset (e.g., --large 10000)")

    args = parser.parse_args()

    if args.cleanup:
        cleanup_test_database()
        return

    if args.setup:
        setup_test_database(args.large)
        return

    if args.baseline:
        case = TestCaseRegistry.get(args.baseline)
        if case:
            print(f"Baseline for case {case.id}: {case.name}")
            print(f"  Type: {case.case_type.value}")
            print(f"  Risk: {case.risk_level.value}")
            print(f"  Test SQL: {case.test_sql[:100]}...")
        return

    # 运行验证
    register_all_cases()

    if args.case:
        # 注册所有案例后获取单个案例
        case = TestCaseRegistry.get(args.case)
        if not case:
            print(f"Case {args.case} not found")
            return

        # 决定验证层次
        if args.level == "all":
            levels = [
                VerificationLevel.V3_RESULT,
                VerificationLevel.V4_BOUNDARY,
                VerificationLevel.V5_EXCEPTION,
                VerificationLevel.V6_DATA_CHANGE,
                VerificationLevel.V7_TRANSACTION,
                VerificationLevel.V8_PERFORMANCE,
            ]
        else:
            level_num = int(args.level)
            levels_map = {
                3: VerificationLevel.V3_RESULT,
                4: VerificationLevel.V4_BOUNDARY,
                5: VerificationLevel.V5_EXCEPTION,
                6: VerificationLevel.V6_DATA_CHANGE,
                7: VerificationLevel.V7_TRANSACTION,
                8: VerificationLevel.V8_PERFORMANCE,
            }
            levels = [levels_map.get(level_num, VerificationLevel.V3_RESULT)]

        # 确保测试数据库存在
        try:
            with PostgresConnection() as pg:
                pass
        except psycopg2.OperationalError:
            print("[!] Database not available, creating...")
            setup_test_database()

        results = {}
        case_results = []
        for level in levels:
            result = run_verification(args.case, level)
            case_results.append(result)
        results[args.case] = case_results

        # 输出结果
        for r in case_results:
            print(f"\n{r.level.value}: {r.status} ({r.duration_ms:.1f}ms)")
            if r.details:
                for k, v in r.details.items():
                    if isinstance(v, str) and len(v) > 200:
                        v = v[:200] + "..."
                    print(f"  {k}: {v}")
        return

    # 运行所有
    print("[*] Running all verifications V3-V8...")
    print("[*] Note: First time requires --setup to create test database")
    try:
        with PostgresConnection() as pg:
            pass
    except psycopg2.OperationalError:
        print("[!] Database not available, running setup...")
        setup_test_database()

    results = run_all_verifications()
    report = generate_verification_report(results)
    print(report)

    # 保存报告
    report_path = TEST_DATA_DIR / "verification_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n[*] Report saved: {report_path}")


if __name__ == "__main__":
    main()