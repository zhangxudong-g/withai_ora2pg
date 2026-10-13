-- ============================================================================
-- 表名: STUDENT_MST
-- 说明: 学生信息主表
-- 版本: 1.0.000
-- ============================================================================
CREATE TABLE STUDENT_MST (
    STUDENT_NO          NUMBER(10,0)      NOT NULL,
    STUDENT_NAME        NVARCHAR2(100)    NOT NULL,
    STUDENT_KANA        NVARCHAR2(200)    NOT NULL,
    BIRTH_DATE          NUMBER(8,0)       NOT NULL,
    GENDER              NUMBER(1,0)       NOT NULL,
    GRADE               NUMBER(2,0)       NOT NULL,
    CLASS_NO            NUMBER(2,0)       NOT NULL,
    ADDRESS             NVARCHAR2(500)    ,
    PHONE               NVARCHAR2(20)     ,
    ENROLL_DATE         NUMBER(8,0)       NOT NULL,
    GRADUATE_DATE       NUMBER(8,0)       ,
    STATUS              NUMBER(1,0)       DEFAULT 0,
    DELETE_FLG          NUMBER(1,0)       DEFAULT 0,
    --
    REC_CREATE_TANTO_CD CHAR(12)         ,
    REC_CREATE_DATE     CHAR(23)          ,
    REC_CREATE_WS       NVARCHAR2(63)    ,
    REC_UPDATE_TANTO_CD CHAR(12)         ,
    REC_UPDATE_DATE     CHAR(23)          ,
    REC_UPDATE_WS       NVARCHAR2(63)    ,
    REC_UPDATE_KBN      CHAR(2)           ,
    CONSTRAINT STUDENT_MST_PK PRIMARY KEY (STUDENT_NO) USING INDEX TABLESPACE USERS
)
TABLESPACE USERS
NOCACHE
LOGGING
;

COMMENT ON TABLE STUDENT_MST IS '学生信息主表';
CREATE INDEX STUDENT_MST_IDX1 ON STUDENT_MST(GRADE, CLASS_NO) TABLESPACE USERS;
CREATE INDEX STUDENT_MST_IDX2 ON STUDENT_MST(STATUS, DELETE_FLG) TABLESPACE USERS;
