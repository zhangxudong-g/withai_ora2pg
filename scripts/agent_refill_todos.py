#!/usr/bin/env python3
"""scripts/agent_refill_todos.py

P4-2 命令行入口：用 Agent 二次转换产物里的 TODO 桩。

P4-1 阶段只做基础设施，本脚本是 P4-2 起手的接入点。
P4-2 工作流：
  1. Convert 产物 -> 找到含 TODO/INLINED 的文件
  2. 逐文件 Snapshot
  3. Orchestrator.process_sql_file -> 重写
  4. validate_pg + 真 PG 试装
  5. 成功 -> 落盘；失败 -> 占位回灌 + 落盘
  6. 写报告：runs/batches/<timestamp>.json

使用：
  # 默认：处理所有 ORACLE / PACKAGE_BODY / FUNCTION / PROCEDURE 产物
  PGPASSWORD=postgres python scripts/agent_refill_todos.py --dry-run

  # 只处理 PACKAGE_BODY
  python scripts/agent_refill_todos.py --targets PACKAGE_BODY

  # 跳过真实试装（快）
  python scripts/agent_refill_todos.py --no-trial-install

  # 指定输出报告
  python scripts/agent_refill_todos.py --report /tmp/batch1.json
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workspace.agent.orchestrator import Orchestrator, BatchResult
from workspace.agent.snapshot import Snapshot
from workspace.agent.schema_dict import scan_existing_functions, build_schema_context

DEFAULT_DST = REPO_ROOT / "oracle_poc" / "output"
DEFAULT_TARGETS = ["FUNCTION", "PACKAGE_BODY", "PROCEDURE", "TRIGGER", "PACKAGE"]
DEFAULT_REPORT = REPO_ROOT / "oracle_poc" / "output" / "_agent_refill_report.json"


def find_todo_files(df_dir: Path, targets: list[str]) -> list[Path]:
    """找含 TODO/INLINED 桩的产物文件。"""
    path: list[Path] = []
    for target in targets:
        sub = df_dir / target
        if not sub.is_dir():
            continue
        for f in sorted(sub.glob("*.sql")):
            text = f.read_text(encoding="utf-8")
            if "TODO" in text or "INLINED" in text:
                path.append(f)
    return path


def refill_one(
    orch: Orchestrator,
    snapshot: Snapshot,
    sql_path: Path,
    write_back: bool,
    trial_install: bool,
) -> list[dict]:
    """处理单文件：snapshot -> 切块 -> process -> 落盘/还原。"""
    sha = snapshot.create(f"P4 batch: {sql_path.name}")
    original_text = sql_path.read_text(encoding="utf-8")
    try:
        from workspace.agent.chunker import chunk_function_or_procedure, ChunkError
        chunks = chunk_function_or_procedure(original_text)
        results = [orch.process_chunk(c) for c in chunks]
    except Exception as e:
        snapshot.restore(sha, files=[str(sql_path.relative_to(snapshot.repo))])
        return [{
            "file": str(sql_path.relative_to(snapshot.repo)),
            "fatal_error": str(e),
        }]

    # 把结果写回文件（如果 write_back）
    if write_back:
        new_text = orch.apply_results_to_text(original_text, chunks, results)
        if new_text != original_text:
            sql_path.write_text(new_text, encoding="utf-8")

    return [
        {
            "file": str(sql_path.relative_to(snapshot.repo)),
            "object_name": r.object_name,
            "object_kind": r.object_kind,
            "success": r.success,
            "errors": r.errors,
            "warnings": r.warnings,
            "quota_used": r.quota_used,
        }
        for r in results
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent refill TODO/INLINED stubs in PG products")
    ap.add_argument("--dst", default=str(DEFAULT_DST), help="产物目录")
    ap.add_argument(
        "--targets", default=",".join(DEFAULT_TARGETS),
        help="逗号分隔的目标子目录（如 FUNCTION,PACKAGE_BODY）",
    )
    ap.add_argument(
        "--write-back", action="store_true",
        help="把转换结果写回产物文件（**谨慎**：P4-2 阶段启用）",
    )
    ap.add_argument("--no-trial-install", action="store_true",
                    help="跳过真 PG 试装（仅正则校验，快）")
    ap.add_argument("--report", default=str(DEFAULT_REPORT),
                    help="批量报告输出路径（JSON）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列目标文件，不处理")
    ap.add_argument("--verify-file-install", action="store_true",
                    help="write-back 后跑整文件 psql 试装（抓跨函数引用错）")
    args = ap.parse_args()

    df_dir = Path(args.dst).resolve()
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    report_path = Path(args.report).resolve()

    if not df_dir.is_dir():
        print(f"[FAIL] 产物目录不存在: {df_dir}", file=sys.stderr)
        return 2

    targets_files = find_todo_files(df_dir, targets)

    if args.dry_run:
        print(f"[dry-run] {len(targets_files)} 个含 TODO/INLINED 的文件：")
        for f in targets_files:
            print(f"  {f.relative_to(df_dir)}")
        return 0

    if not targets_files:
        print("[INFO] 没有含 TODO/INLINED 的文件（已完成或不需要清理）")
        return 0

    print(f"[start] 处理 {len(targets_files)} 个文件，targets={targets}")

    # P4-2 关键：扫描已存在的函数字典，注入 prompt（避免 Agent 引用未 lift 的嵌套函数）
    existing_funcs = scan_existing_functions(df_dir)
    schema_ctx = build_schema_context(existing_funcs)
    print(f"[info] 可用函数字典：{len(existing_funcs)} 个函数已注入 prompt")

    validate_kwargs = {"trial_install": not args.no_trial_install}
    orch = Orchestrator(validate_kwargs=validate_kwargs, schema_context=schema_ctx)
    snapshot = Snapshot()

    start_ts = time.time()
    all_results: list[dict] = []
    for i, f in enumerate(targets_files, 1):
        print(f"[{i+1}/{len(targets_files)}] {f.relative_to(df_dir)}", end=" ... ", flush=True)
        try:
            results = refill_one(
                orch, snapshot, f,
                write_back=args.write_back,
                trial_install=not args.no_trial_install,
            )
            all_results.extend(results)

            # 文件级试装（write-back 后跑整文件 psql，抓跨函数引用错）
            if args.write_back and args.verify_file_install:
                import subprocess as sp
                psql_bin = os.environ.get("PSQL_BIN", "psql")
                verify_sql = (
                    "BEGIN;\n"
                    "CREATE SCHEMA IF NOT EXISTS agent_verify;\n"
                    "SET search_path TO agent_verify;\n"
                    + f.read_text(encoding="utf-8") +
                    "\nROLLBACK;\n"
                )
                proc = sp.run(
                    [psql_bin, "-h", "localhost", "-U", "postgres",
                     "-d", "postgres", "-X", "-q",
                     "-v", "ON_ERROR_STOP=on", "-c", verify_sql],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    env={**os.environ,
                         "PGPASSWORD": os.environ.get("PGPASSWORD", ""),
                         "PGCLIENTENCODING": "UTF8"},
                    timeout=30,
                )
                if proc.returncode != 0:
                    err_lines = [l for l in (proc.stdout + proc.stderr).splitlines()
                                 if "错误" in l or "ERROR" in l][:3]
                    print(f"  FILE-INSTALL FAIL: {'; '.join(err_lines)}")
                    # 不自动还原（i_vsql 等老错可能被误判）；由用户决定
                    all_results.append({
                        "file": str(f.relative_to(df_dir)),
                        "file_install_error": err_lines,
                    })
                else:
                    print(f"  FILE-INSTALL OK ({len(results)} chunks)")
            else:
                print(f"{len(results)} chunks")
        except Exception as e:
            print(f"FAIL: {e}")
            all_results.append({"file": str(f), "fatal_error": str(e)})

    elapsed = time.time() - start_ts
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "targets": targets,
        "trial_install": not args.no_trial_install,
        "write_back": args.write_back,
        "files_processed": len(targets_files),
        "results": all_results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"[done] 报告: {report_path}")
    print(f"[stats] {sum(r.get('quota_used', 0) for r in all_results)} 次配额消耗")
    return 0


if __name__ == "__main__":
    sys.exit(main())