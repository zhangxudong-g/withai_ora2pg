# PL/SQL 转换失败分析报告（237 PASS / 9 FAIL + 业务覆盖脚本崩溃）

> 分析对象：verify_all 汇总中的「存储过程专项 49 项」（9 个 PLSQL 对象 FAIL）与
> 「★ 业务 100% 覆盖 93 项」（`scripts/verify_business_coverage_100.py` 崩溃）。
> 本报告仅分析根因与修改方案，不修改代码。

---

## 一、总体判断

9 个失败对象 + 业务覆盖脚本崩溃，**根因高度集中**：

- `convert/plsql_converter.py` 中通过的对象（KHTCLR / KHTSET / KZISKCHK /
  SKJDOG / SKJDOG2）都是人工编写的「专用策略」（见 `convert/plsql_converter.py:396` 起
  「专用策略: 包含嵌套函数 / PROCEDURE / %ROWTYPE / REF CURSOR 等复合结构」区块）。
- 失败的 9 个对象全部落到了**通用规则式回退路径**
  （`parse_declarations` `:110`、`convert_function` `:151`）。
  该路径是「逐行正则 → 一行一个变量声明」的简陋解析，
  遇到下列 4 类复杂结构必然出错：

| 缺陷类                                   | 通用路径的问题                                                     | 命中的对象                                |
| ---------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------- |
| 1. 多行嵌套子程序头                      | `FUNCTION f(...) RETURN T IS` / `PROCEDURE p(a IN T,...)` 跨行头被逐行吞成假变量声明，函数体整段丢失 | GKBFKGZSHNT、GKBSBCHKLCSV、GKBSKIDOTCH    |
| 2. `%TYPE` / `%ROWTYPE`                  | 变量声明正则只取第一个词，`表.列%TYPE` 截断成裸表名当类型          | GKBFKHMCTRL（GKBSBIDOIRCSV 有同类隐患）   |
| 3. REF CURSOR 别名 / 具名游标             | `TYPE x IS REF CURSOR` 行被跳过（`:122`）但变量行保留原样；`CURSOR c IS SELECT <多行>` 被撕碎成垃圾行 | GKBSBIDOIRCSV、GKBSKGYMSHKK               |
| 4. 库外自定义类型 / 注释型 DDL            | `A_CONS_PRM`（Oracle 公共层类型，`source/type/` 里没有）原样保留、PG 端从未创建；TABLE OF 类型转换只输出 8 行注释 → execute 空语句 | GKBFKGKCDGET、GKBFKGKKUGET、GKBEKENSU_LIST |

---

## 二、逐条根因与证据

### 1. GKBEKENSU_LIST — `can't execute an empty query`

- `source/type/GKBEKENSU_LIST.SQL` 是 Oracle 嵌套表类型
  `TYPE ... AS TABLE OF GKBEKENSU_RECORD`；
  `out/plsql/GKBEKENSU_LIST.pgsql` 只有 8 行注释建议，无任何可执行 SQL
  → psycopg2 执行纯注释串报空查询。
- **方案**：
  1. 类型转换器对此类类型输出真实 DDL，推荐
     `CREATE DOMAIN gkbekensu_list AS gkbekensu_record[];`
     （PG 可对 composite type 建数组、domain 可当声明类型，
     其他对象里 `x GKBEKENSU_LIST` 的引用才能成立）；
  2. 验证脚本对「去注释后无可执行语句」的文件记录 SKIP 而非崩溃/FAIL。
  两条都做。

### 2. GKBFKGKCDGET / GKBFKGKKUGET — 类型 `a_cons_prm` 不存在

- 源码 `g_tITEM A_CONS_PRM`（`source/function/GKBFKGKCDGET.SQL:31`），
  `A_CONS_PRM` 是 GAAXXX 公共层类型，不在 `source/type/`，
  转换器原样保留 → PG 无此类型。
- 且正文有 VARRAY 式取值 `g_tITEM(1)`，PG composite 也不支持。
- **方案**（二选一）：
  - 专用策略重写（推荐，与现有 KHTCLR 写法一致）：
    `KKAPK0030.FCTGetR` 语义就是取第 N 个参数值，
    把 `A_CONS_PRM` 出参改为 `OUT varchar`，消掉 `g_titem(1)`；
  - 或在 DDL 层建兼容类型（如 `CREATE DOMAIN a_cons_prm AS varchar[];`）
    + 转换器加 `col(n)` → 数组下标重写规则。
  - 无论哪种，转换器都要增加**未知类型守卫**：
    声明段里的类型若不在 PG 映射表 / 已转 DDL / 已知公共类型注册表中就显式报错，
    杜绝「静默原样通过」。

### 3. GKBFKGZSHNT — 语法错误在 `NUMBER`

- `out/plsql/GKBFKGZSHNT.pgsql:56` 的
  `function FUNC_JICHINAME( PSANTEIDANTAI_CD IN NUMBER );` 是伪造的行：
  `parse_declarations` 用 `IS\s+(.*?)\bBEGIN` 抓「声明段」，
  但抓到的是嵌套函数头（源 `GKBFKGZSHNT.SQL:90` 的
  `FUNCTION FUNC_JICHINAME(...) RETURN NVARCHAR2 IS`），
  逐行正则把函数头吃成「变量 FUNCTION，类型 FUNC_JICHINAME(...)」——
  NUMBER 未映射、缺 `RETURNS`、多行签名断裂。
- **更严重的问题**：`convert_function` 主 BEGIN 定位逻辑
  （`plsql_converter.py:163-197`）按「跳过 FUNCTION 块找下一个 BEGIN」，
  但源里还有嵌套 `PROCEDURE PROC_INITIALIZE`（`:109` 起），
  主 BEGIN 会错误锁定到嵌套体的 BEGIN，导致**外层函数主体整个丢失**——
  当前输出的「函数体」其实是某个嵌套块的残骸，
  即使修好语法，这个函数也是错实现。
- **方案**：通用路径先做块级（BEGIN/END 深度）解析，声明段按块切分而非逐行；
  本对象按现有模式写专用策略
  （内含嵌套函数 + 多嵌套过程，通用规则无法忠实转换）。

### 4. GKBFKHMCTRL — 类型 `gabtatenakihon` 不存在

- 源是 `g_rSHIMEI_KANA GABTATENAKIHON.SHIMEI_KANA%TYPE`
  （`source/function/GKBFKHMCTRL.SQL:50-54` 共 5+ 处），
  `%TYPE` 后缀被截断，声明变成 `g_rshimei_kana GABTATENAKIHON;`。
- **方案**：
  - 识别 `%TYPE`：查已转 DDL 取真实列类型再映射
    （转换器已有查 DDL 列的辅助函数 `_read_ddl_columns` `:618`，可直接复用）；
  - `%ROWTYPE`：在 DDL 阶段生成 `CREATE TYPE t_xxx_row AS (LIKE xxx);`，
    声明处用 `xxx_row`。

### 5. GKBSBCHKLCSV — 语法错误在 `V_WKORDER`

- 报错点在下一行，真凶是 `out/plsql/GKBSBCHKLCSV.pgsql:27` 的
  `return VARCHAR;`——嵌套函数 `FUNCTION FUNC_GET_SORT_ORDER`
  的跨行头（源 `GKBSBCHKLCSV.SQL:48-55`：
  `FUNCTION ... / RETURN NVARCHAR2 / IS / V_WKORDER VARCHAR2(3000);`）被逐行吞：
  `RETURN NVARCHAR2` 变成名为 `return`（PG 保留字）的变量声明，
  解析错误在下一行 V_WKORDER 才爆发。嵌套函数体同样丢失。
  **与 #3 同一 bug 家族。**
- **方案**：同 #3——声明段块感知解析 + 多行嵌套子程序头处理；
  本对象适合专用策略。

### 6. GKBSBIDOIRCSV — 类型 `ref_cur_doutai` 不存在

- 源 `source/procedure/GKBSBIDOIRCSV.SQL:45-46`：
  `TYPE ref_CUR_DOUTAI IS REF CURSOR; C1 ref_CUR_DOUTAI;`。
  通用路径跳过 TYPE 行（`plsql_converter.py:122`）但保留变量行且不替换类型。
- **方案**：变量声明的类型若是同声明段内 REF CURSOR 别名 →
  改写为 PG 内建 `c1 REFCURSOR;`，
  后续 `OPEN c1 FOR vsql / FETCH / CLOSE` PG 天然支持，可直通。
- ⚠️ **连带隐患**（修好本项后的下一个错）：
  - `GKBSBIDOIRCSV.SQL:47-48` 的
    `CR1 GKBWIDOICHIRAN%ROWTYPE`、`r_WKRECORD001 GKBWL280R001%ROWTYPE`
    与 #4 同类（输出里已是 `cr1 GKBWIDOICHIRAN;`）；
  - `:158` 的动态 SQL 里 `INSERT INTO ... VALUES r_WKRECORD001`
    是 Oracle record 插值语法，PG 需 `VALUES (r)` 形式。
  需一并处理。

### 7. GKBSKGYMSHKK — 类型 `cgakureibo` 不存在

- 源 `source/procedure/GKBSKGYMSHKK.SQL:28-33` 是真正的具名游标
  `CURSOR CGAKUREIBO IS SELECT A.* FROM GKBTGAKUREIBO A WHERE ...`，
  通用路径撕裂成 `cursor CGAKUREIBO;` / `select A;` / `order BY;`
  （`out/plsql/GKBSKGYMSHKK.pgsql:23-28` 可见残骸）。
- **方案**（二选一）：
  1. 声明段识别多行具名游标，输出 PG 具名游标
     `cgakureibo IS SELECT A.* FROM gkbtgakureibo A WHERE (...);`
     （WHERE 引用外层函数参数，合法）；
  2. 按 `plsql_converter.py:540` 注释里既有惯例
     （REF CURSOR 循环 → FOR 循环）把 OPEN/FETCH/EXIT 循环整体改写为
     `FOR rgakureibo IN SELECT ... LOOP`，删掉具名游标，更稳。

### 8. GKBSKIDOTCH — 语法错误在 `NVARCHAR2`

- 嵌套 `PROCEDURE PROC_DAYEDIT(i_sDATE IN NVARCHAR2, o_sDATE OUT NVARCHAR2,
  RetValue OUT PLS_INTEGER)`（源 `GKBSKIDOTCH.SQL:64`，
  其调用点 `:119` 起全部被注释，是死代码）头被吞成伪声明，
  **参数列表内的类型完全没做映射**（NVARCHAR2/PLS_INTEGER 原样）。
- **方案**：声明段对 `PROCEDURE/FUNCTION name(<参数列表>)` 跨行签名做识别，
  至少参数列表内也跑类型映射；
  鉴于该嵌套过程是死代码，专用策略里直接删除（保留注释说明）最干净。

### 9. `verify_business_coverage_100.py` 崩溃（93 项全军覆没）

- `:79` 对 `DDL_DIR/PLSQL_DIR` 里每个 `.pgsql` 裸
  `cur.execute(f.read_text())`，无 try/except、无空 SQL 检查，
  命中纯注释的 `GKBEKENSU_LIST` 即
  `psycopg2.ProgrammingError: can't execute an empty query` 终止——
  与 [3] 中存储过程专项里被逐项捕获的 GKBEKENSU_LIST FAIL 是同一个文件。
  对比 `scripts/verify_complete_coverage.py:85-99` 是有逐文件 try/except 的。
- **方案**（双层）：
  1. 该脚本加空 SQL 检查（`continue` 记 SKIP）+ 逐文件 try/except
     （对齐 `verify_complete_coverage.py` 的现有写法）；
  2. 转换器侧按 #1 输出可执行 DDL，从源头消除纯注释文件。

---

## 三、修复优先级建议

1. **P0 — 解锁 93 项脚本**
   - `verify_business_coverage_100.py` 的空 SQL 守卫 + 逐文件 try/except；
   - `GKBEKENSU_LIST` 改为可执行 DDL（domain over array）。
2. **P1 — 通用转换器缺陷（一处修复覆盖 #3/#5/#6/#7/#8 全部）**
   声明段做块感知解析：
   - 多行嵌套子程序头；
   - 具名游标 `CURSOR ... IS SELECT <多行>`；
   - `%TYPE` / `%ROWTYPE`（查 DDL 取真类型）;
   - REF CURSOR 别名 → `REFCURSOR`；
   - 参数列表类型映射；
   - 未知类型显式报错。
3. **P2 — 逐对象专用策略**
   - GKBFKGZSHNT（主体丢失，通用路径已产生**错误实现**，
     即使语法修好也不可用，必须专用策略重写并对照原逻辑验证）；
   - GKBFKGKCDGET / GKBFKGKKUGET（按 A_CONS_PRM 真实语义重写）。
   这与现有 KHTCLR / KZISKCHK / SKJDOG 的工程模式一致——
   PoC 阶段复杂结构对象用人工专用策略比把通用规则做全更可靠。

---

## 附：错误 → 根因速查表

| 对象                    | 报错                                   | 根因编号 |
| ----------------------- | -------------------------------------- | -------- |
| GKBEKENSU_LIST          | can't execute an empty query           | #1       |
| GKBFKGKCDGET            | 类型 "a_cons_prm" 不存在               | #2       |
| GKBFKGKKUGET            | 类型 "a_cons_prm" 不存在               | #2       |
| GKBFKGZSHNT             | 语法错误 在 "NUMBER"                   | #3       |
| GKBFKHMCTRL             | 类型 "gabtatenakihon" 不存在           | #4       |
| GKBSBCHKLCSV            | 语法错误 在 "V_WKORDER"（实为 `return VARCHAR;`） | #5       |
| GKBSBIDOIRCSV           | 类型 "ref_cur_doutai" 不存在           | #6       |
| GKBSKGYMSHKK            | 类型 "cgakureibo" 不存在               | #7       |
| GKBSKIDOTCH             | 语法错误 在 "NVARCHAR2"                | #8       |
| verify_business_coverage_100.py | ProgrammingError 崩溃（93 项未执行） | #9（同 #1 文件） |
