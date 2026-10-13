"""
workspace 配置

路径和参数统一管理。
"""
from pathlib import Path

# 基础路径
BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE = BASE_DIR
REPO_ROOT = BASE_DIR.parent

# 输入输出
INPUT_DIR = WORKSPACE / "input"
OUTPUT_DIR = WORKSPACE / "output"
RESULTS_DIR = WORKSPACE / "results"
LATEST_DIR = RESULTS_DIR / "latest"

# 转换引擎
PERLSTYLE_DIR = REPO_ROOT / "ora2pg_py_perlstyle"

# PostgreSQL 配置
PG_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "postgres",
    "user": "postgres",
    "password": "postgres",
}

# 验证沙箱
PG_TEST_SCHEMA = "workspace_verify"

# 验证权重
SCORE_WEIGHTS = {
    "L1": 0.15,
    "L2": 0.20,
    "L3": 0.25,
    "L4": 0.40,
}

# Gate 配置
GATE = {
    "min_case_coverage": 0.5,        # L4 可执行用例占比下限
    "fail_on_logic_error": True,
    "fail_on_parse_error": True,
    "fail_on_install_error": True,
    "require_cases": True,
}

# 文件匹配模式
SQL_PATTERNS = ("*.sql", "*.SQL", "*.pks", "*.pkb", "*.fnc", "*.prc", "*.trg", "*.vw")


def ensure_dirs() -> None:
    """确保所有目录存在"""
    for d in (INPUT_DIR, OUTPUT_DIR, RESULTS_DIR, LATEST_DIR):
        d.mkdir(parents=True, exist_ok=True)
