-- ============================================================================
-- 包规格: PKG_SCORE_MANAGE - 成绩管理完整包
-- 版本: 1.0.000
-- ============================================================================
CREATE OR REPLACE PACKAGE PKG_SCORE_MANAGE
--1.0.100.000-02.02-02.02
IS
    ------------------------------------------------------------------------
    -- 全局常量定义
    ------------------------------------------------------------------------
    c_ONLINE           CONSTANT PLS_INTEGER := 1;     -- 在线处理
    c_BATCH            CONSTANT PLS_INTEGER := 2;     -- 批次处理
    c_OK               CONSTANT PLS_INTEGER := 0;     -- 正常
    c_ERR              CONSTANT PLS_INTEGER := -1;    -- 异常
    c_WARN             CONSTANT PLS_INTEGER := 1;     -- 警告

    ------------------------------------------------------------------------
    -- 全局变量定义
    ------------------------------------------------------------------------
    g_nJOBNUM              NUMBER;                     -- 批次号
    g_sWSNUM               NVARCHAR2(63);             -- 工作站号
    g_sTANTO_CD            CHAR(12);                   -- 担当者代码
    g_sSTARTDATE           NVARCHAR2(23);             -- 开始时间
    g_sMESSAGE             NVARCHAR2(4000);           -- 消息
    g_nPASS_SCORE          NUMBER := 60;              -- 及格分数线
    g_nEXCELLENT_SCORE     NUMBER := 90;              -- 优秀分数线
    g_nGOOD_SCORE          NUMBER := 80;              -- 良好分数线

    ------------------------------------------------------------------------
    -- 在线处理入口
    ------------------------------------------------------------------------
    PROCEDURE PONLINE(
        i_sWSNUM          IN  NVARCHAR2,
        i_sTANTO_CD      IN  CHAR,
        i_sPARAM         IN  NVARCHAR2,
        o_nRESULT        OUT NUMBER,
        o_sMESSAGE       OUT NVARCHAR2
    );

    ------------------------------------------------------------------------
    -- 批次处理入口
    ------------------------------------------------------------------------
    PROCEDURE PBATCH(
        i_nJOBNUM        IN  NUMBER,
        i_sWSNUM         IN  NVARCHAR2,
        i_sTANTO_CD     IN  CHAR,
        i_nNENDO         IN  NUMBER,
        i_nGRADE         IN  NUMBER,
        i_nCLASS_NO      IN  NUMBER,
        i_nEXAM_TYPE     IN  NUMBER,
        o_nRTN           OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 成绩处理
    ------------------------------------------------------------------------
    PROCEDURE P_PROCESS_SCORE(
        i_nSTUDENT_NO      IN  NUMBER,
        i_nSUBJECT_CD      IN  NUMBER,
        i_nEXAM_DATE       IN  NUMBER,
        i_nEXAM_TYPE       IN  NUMBER,
        i_nSCORE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 批量成绩处理 (使用BULK COLLECT)
    ------------------------------------------------------------------------
    PROCEDURE P_BATCH_PROCESS_SCORE(
        i_vWSNUM         IN  VARCHAR2,
        i_vTANTO_CD     IN  VARCHAR2,
        i_nEXAM_DATE    IN  NUMBER,
        i_nEXAM_TYPE    IN  NUMBER,
        o_nRTN          OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 生成成绩报表
    ------------------------------------------------------------------------
    PROCEDURE P_GEN_REPORT(
        i_nNENDO           IN  NUMBER,
        i_nGRADE           IN  NUMBER,
        i_nCLASS_NO        IN  NUMBER,
        i_vWSNUM           IN  VARCHAR2,
        o_nRTN             OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 更新排名
    ------------------------------------------------------------------------
    PROCEDURE P_UPDATE_RANK(
        i_nGRADE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 批量更新排名 (FORALL)
    ------------------------------------------------------------------------
    PROCEDURE P_BATCH_UPDATE_RANK(
        i_nGRADE           IN  NUMBER,
        o_nRTN             OUT NUMBER
    );

    ------------------------------------------------------------------------
    -- 导出CSV
    ------------------------------------------------------------------------
    PROCEDURE P_EXPORT_CSV(
        i_vWSNUM         IN  VARCHAR2,
        i_vFILENAME      IN  VARCHAR2,
        o_nRTN           OUT NUMBER,
        o_vFILE_PATH    OUT VARCHAR2
    );

    ------------------------------------------------------------------------
    -- 获取学生成绩列表
    ------------------------------------------------------------------------
    FUNCTION F_GET_SCORE_LIST(
        i_nSTUDENT_NO    IN  NUMBER
    ) RETURN SCORE_LIST;

    ------------------------------------------------------------------------
    -- 获取学生总成绩
    ------------------------------------------------------------------------
    FUNCTION F_GET_TOTAL(
        i_nSTUDENT_NO  IN  NUMBER
    ) RETURN NUMBER;

    ------------------------------------------------------------------------
    -- 获取学生平均成绩
    ------------------------------------------------------------------------
    FUNCTION F_GET_AVG(
        i_nSTUDENT_NO  IN  NUMBER
    ) RETURN NUMBER;

    ------------------------------------------------------------------------
    -- 获取学生排名
    ------------------------------------------------------------------------
    FUNCTION F_GET_RANK(
        i_nSTUDENT_NO  IN  NUMBER,
        i_nRANK_TYPE   IN  NUMBER
    ) RETURN NUMBER;

END PKG_SCORE_MANAGE;
/
