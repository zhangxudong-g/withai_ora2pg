-- ============================================================================
-- 触发器 (Oracle PL/SQL特有)
-- ============================================================================

-- ============================================================================
-- 1. 学生表 - 插入前触发器
-- ============================================================================
CREATE OR REPLACE TRIGGER TRG_STUDENT_BI
BEFORE INSERT ON STUDENT_MST
FOR EACH ROW
DECLARE
    vMAX_STUDENT_NO STUDENT_MST.STUDENT_NO%TYPE;
BEGIN
    -- 如果没有指定学生编号，使用序列
    IF :NEW.STUDENT_NO IS NULL OR :NEW.STUDENT_NO = 0 THEN
        SELECT NVL(MAX(STUDENT_NO), 10000) + 1
        INTO vMAX_STUDENT_NO
        FROM STUDENT_MST;
        :NEW.STUDENT_NO := vMAX_STUDENT_NO;
    END IF;

    -- 设置创建信息
    :NEW.REC_CREATE_DATE := TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3');
    :NEW.REC_UPDATE_DATE := TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3');
    
    -- 默认状态
    IF :NEW.STATUS IS NULL THEN
        :NEW.STATUS := 0;
    END IF;
    IF :NEW.DELETE_FLG IS NULL THEN
        :NEW.DELETE_FLG := 0;
    END IF;
END;
/

-- ============================================================================
-- 2. 学生表 - 更新后触发器
-- ============================================================================
CREATE OR REPLACE TRIGGER TRG_STUDENT_AU
AFTER UPDATE ON STUDENT_MST
FOR EACH ROW
DECLARE
    PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
    -- 记录状态变更日志
    IF :OLD.STATUS != :NEW.STATUS THEN
        INSERT INTO PROCESS_LOG (
            LOG_ID, JOBNUM, PROCESS_NAME, STEP_NO, LOG_LEVEL,
            LOG_MESSAGE, SQLCODE, TANTO_CD, LOG_DATE
        ) VALUES (
            SEQ_LOG_ID.NEXTVAL, NULL, 'STUDENT_STATUS_CHANGE',
            1, 0, '学生' || :NEW.STUDENT_NO || '状态变更: ' || :OLD.STATUS || ' -> ' || :NEW.STATUS,
            0, USER, TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3')
        );
        COMMIT;
    END IF;
END;
/

-- ============================================================================
-- 3. 成绩表 - 插入/更新前触发器(计算等级和及格标志)
-- ============================================================================
CREATE OR REPLACE TRIGGER TRG_SCORE_BIU
BEFORE INSERT OR UPDATE ON SCORE_REC
FOR EACH ROW
DECLARE
    vPASS_SCORE SUBJECT_MST.PASS_SCORE%TYPE := 60;
BEGIN
    -- 获取科目及格分数线
    BEGIN
        SELECT NVL(PASS_SCORE, 60)
        INTO vPASS_SCORE
        FROM SUBJECT_MST
        WHERE SUBJECT_CD = :NEW.SUBJECT_CD;
    EXCEPTION
        WHEN OTHERS THEN
            vPASS_SCORE := 60;
    END;

    -- 计算是否及格
    IF :NEW.SCORE IS NOT NULL THEN
        IF :NEW.SCORE >= vPASS_SCORE THEN
            :NEW.IS_PASS := 1;
        ELSE
            :NEW.IS_PASS := 0;
        END IF;

        -- 计算等级
        IF :NEW.SCORE >= 90 THEN
            :NEW.GRADE_LEVEL := 1;  -- 优秀
        ELSIF :NEW.SCORE >= 80 THEN
            :NEW.GRADE_LEVEL := 2;  -- 良好
        ELSIF :NEW.SCORE >= 70 THEN
            :NEW.GRADE_LEVEL := 3;  -- 中等
        ELSIF :NEW.SCORE >= 60 THEN
            :NEW.GRADE_LEVEL := 4;  -- 及格
        ELSE
            :NEW.GRADE_LEVEL := 5;  -- 不及格
        END IF;
    END IF;

    -- 设置更新信息
    :NEW.REC_UPDATE_DATE := TO_CHAR(SYSTIMESTAMP, 'YYYY/MM/DD HH24:MI:SS.FF3');
END;
/

-- ============================================================================
-- 4. 配置表 - 行级安全触发器
-- ============================================================================
CREATE OR REPLACE TRIGGER TRG_SYSTEM_CONFIG_BD
BEFORE DELETE ON SYSTEM_CONFIG
FOR EACH ROW
DECLARE
    vCOUNT NUMBER;
BEGIN
    -- 禁止删除系统配置(类型=1)
    IF :OLD.CONFIG_TYPE = 1 THEN
        RAISE_APPLICATION_ERROR(-20001, '系统配置不能删除!');
    END IF;
END;
/

-- ============================================================================
-- 5. 复合触发器 - 成绩表批量处理
-- ============================================================================
CREATE OR REPLACE TRIGGER TRG_SCORE_COMPOUND
FOR INSERT OR UPDATE OR DELETE ON SCORE_REC
COMPOUND TRIGGER
    -- 声明节
    TYPE t_score_list IS TABLE OF SCORE_REC%ROWTYPE INDEX BY PLS_INTEGER;
    v_scores t_score_list;
    v_count NUMBER := 0;

    -- 每个行处理前
    BEFORE EACH ROW IS
    BEGIN
        v_count := v_count + 1;
        v_scores(v_count).STUDENT_NO := :NEW.STUDENT_NO;
        v_scores(v_count).SUBJECT_CD := :NEW.SUBJECT_CD;
        v_scores(v_count).SCORE := :NEW.SCORE;
    END BEFORE EACH ROW;

    -- 语句执行后
    AFTER STATEMENT IS
    BEGIN
        DBMS_OUTPUT.PUT_LINE('处理了 ' || v_count || ' 条成绩记录');
    END AFTER STATEMENT;
END TRG_SCORE_COMPOUND;
/

-- ============================================================================
-- 6. INSTEAD OF 触发器 - 用于视图更新
-- 注意: V_STUDENT_WITH_SCORE 视图已在 VIEW/00_VIEW.sql 中定义
-- ============================================================================

CREATE OR REPLACE TRIGGER TRG_V_STUDENT_IO
INSTEAD OF INSERT OR UPDATE ON V_STUDENT_WITH_SCORE
FOR EACH ROW
BEGIN
    IF INSERTING THEN
        INSERT INTO STUDENT_MST (STUDENT_NO, STUDENT_NAME, GRADE, CLASS_NO)
        VALUES (:NEW.STUDENT_NO, :NEW.STUDENT_NAME, :NEW.GRADE, :NEW.CLASS_NO);
    ELSIF UPDATING THEN
        UPDATE STUDENT_MST
        SET STUDENT_NAME = :NEW.STUDENT_NAME,
            GRADE = :NEW.GRADE,
            CLASS_NO = :NEW.CLASS_NO
        WHERE STUDENT_NO = :NEW.STUDENT_NO;
    END IF;
END;
/
