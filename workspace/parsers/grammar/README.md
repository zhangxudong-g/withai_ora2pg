# workspace/parsers/grammar —— ANTLR PL/SQL 解析器（生产副本）

## 这是什么

ANTLR 4 生成的 PL/SQL 语法解析器，**本项目的生产副本**。用于 AST 层的结构体检
（对象数量比对、嵌套深度检测、空壳桩识别），**不参与产物生成**。

## 来源与版本（provenance）

| 项 | 值 |
|---|---|
| 生成工具 | **ANTLR 4.13.2** |
| 运行时 | `antlr4-python3-runtime==4.13.2`（**必须严格同版本**）|
| grammar 源 | `PlSqlParser.g4`（社区 grammar，**不在本仓库**，见下方说明）|
| 原始位置 | `pipeline/parsers/grammar/`（前期代码，已冻结）|
| 复制日期 | 2026-10-09 |
| 复制方式 | 字节级复制，6 个文件 SHA-256 全部一致 |

### 文件清单

| 文件 | 大小 | 作用 |
|---|---|---|
| `PlSqlLexer.py` | 1.2 MB | 词法分析器 |
| `PlSqlParser.py` | 6.9 MB | 语法分析器 |
| `PlSqlParserListener.py` | 410 KB | 监听器接口 |
| `PlSqlParserVisitor.py` | 239 KB | 访问者接口 |
| `PlSqlParserBase.py` | 2.0 KB | 版本判定辅助类 |
| `PlSqlLexerBase.py` | 165 B | 换行判定辅助类 |

## ⚠️ 重要约束

### 1. **本目录为生成物，禁止手工编辑**

这些文件由 ANTLR 自动生成。任何手改都会在下次重新生成时被覆盖，
且可能导致与 runtime 版本不匹配而整体失效。

**要改解析行为，去改 `workspace/parsers/structure_check.py` 等上层代码，不要动这里的文件。**

### 2. **`.g4` 源文件不在本仓库，无法重新生成**

生成器源文件位于仓库外（本机路径 `D:/code-wiki/experiments/grammars/community/PlSqlParser.g4`，
**已确认该路径当前不存在**）。这意味着：

- **无法在本仓库内重新生成这些文件**
- 只能复制，不能"改 grammar 后重新生成"
- 若未来需要改 grammar，必须先取得 `.g4` 源文件并纳入版本管理

> 这是已知的可维护性缺口。T15 防复发工程化时应评估：
> 是否把 `.g4` 纳入版本管理，或改用可重现的生成脚本 + 锁定依赖。

### 3. **runtime 版本必须严格匹配**

ANTLR 的 Python runtime 与生成代码强绑定。实测 `4.13.2` 生成的文件配 `4.13.2` runtime 正常工作；
若 runtime 降到 4.11 或升到 4.13，会出现 `AttributeError` 或反序列化失败。

**升级 runtime 前必须先用本目录做一次解析冒烟测试。**

### 4. **已知解析缺口**

实测 `oracle_poc/01.SQL/PACKAGE_BODY/00_PACKAGE_BODY.sql` 有 **8 处语法错误**，
根因是**嵌套子程序缺少 `DECLARE` 关键字**（Oracle 允许嵌套函数直接跟在 `BEGIN` 之后，
ANTLR 社区 grammar 要求 `declareBlock` 必须在先）。

- 这是**同一根因的连锁报错**，不是 8 个独立缺陷
- 错误位置约在 `L161`（`FUNCTION GEN_NEW_JOBNUM`），连锁至 `L169`
- 其他文件实测均为 **0 错误**：
  - `TRIGGER/00_TRIGGER.sql` — 6 unit，0 err
  - `FUNCTION/00_FUNCTION.sql` — 0 err
  - `PROCEDURE/00_PROCEDURE.sql` — 0 err
- **对策**：解析前对嵌套区域做预处理（补 `DECLARE`），或该区域退回文本级处理。
  见 `workspace/parsers/structure_check.py` 的降级策略。

### 5. **编码注意**

`PlSqlParserBase.py` 与 `PlSqlLexerBase.py` 中的中文注释存在编码损坏（GBK/UTF-8 混淆，
如"浣跨敤鐩稿"实为"使用相对路径"）。这来自上游生成器，**不影响功能**（均为注释），
但若将来重新生成，建议在生成后统一修正编码。

## 用途与限制

**用来做**（只读体检，不改产物）：
- 统计源文件中的对象数量 → 与产物比对（DoD-7 产出完整性）
- 检测嵌套子程序深度 → 识别"被压平成注释"的情况
- 提取触发器元数据（名字/时机/事件/宿主表）→ 结构化转换参考
- 溯源 `%TYPE` 引用了哪张表哪个列

**不用来做**：
- ❌ **不做产物生成** —— 慢约 2500 倍（规则引擎 4.7ms/文件 vs ANTLR 11700ms/文件）
- ❌ 不进转换主路径 —— 只在转换**完成后**跑一次体检（约 48s 扫全部语料）

## 快速验证

确认本副本可独立工作（不依赖 `pipeline/`）：

```python
import sys
sys.path.insert(0, "workspace/parsers/grammar")
from antlr4 import InputStream, CommonTokenStream
from PlSqlLexer import PlSqlLexer
from PlSqlParser import PlSqlParser

src = open("oracle_poc/01.SQL/TRIGGER/00_TRIGGER.sql", encoding="utf-8").read()
lex = PlSqlLexer(InputStream(src))
tree = PlSqlParser(CommonTokenStream(lex)).sql_script()
print("unit 数:", len([c for c in tree.getChildren()
                       if type(c).__name__ == 'Unit_statementContext']))
# 期望输出: unit 数: 6
```