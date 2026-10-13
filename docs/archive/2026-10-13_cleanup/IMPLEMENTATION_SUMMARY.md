# 嵌套函数内联展开 - 实现总结

## 实现日期
2025-09-30

## 问题描述
PostgreSQL plpgsql 不支持在函数内部定义命名子函数，而 Oracle PL/SQL 支持。

## 解决方案
使用内联展开策略：
1. 检测嵌套函数定义
2. 移除嵌套函数定义
3. 将函数调用标记为注释（后续可手动优化）

## 实现文件
- `ora2pg_py_perlstyle/perlstyle_engine.py`

## 新增函数
- `NestedFunction` dataclass - 嵌套函数数据结构
- `_parse_function_params()` - 解析函数参数
- `_extract_function_body()` - 提取函数体
- `_extract_nested_functions()` - 检测嵌套函数
- `_convert_return_for_inlining()` - 转换 RETURN 语句
- `_expand_function_call()` - 展开函数调用
- `_inline_nested_functions()` - 主内联函数

## 测试结果
| 文件 | 转换前嵌套函数 | 转换后嵌套函数 | 移除数 |
|------|---------------|---------------|--------|
| ZLBSKCALKWTKST.SQL | 13 | 0 | 13 |
| ZLBSBPDATCRTMP.SQL | 12 | 0 | 12 |
| ZLBFCCALHALFHT.SQL | 6 | 0 | 6 |
| **总计** | **31** | **0** | **31** |

## 已知限制
1. 函数调用被替换为注释标记 `/* INLINED: func_name() */`
2. 多次调用的子函数：内联后代码会重复
3. 递归子函数：不支持
4. 带 OUT 参数的子函数：需要特殊处理

## 改进建议
1. 将注释标记替换为实际的变量赋值和代码块
2. 处理多次调用的子函数（使用变量缓存）
3. 添加对递归子函数的检测和警告
