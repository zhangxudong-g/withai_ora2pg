-- Perl-style Ora2Py Warnings:
-- SYNONYM SM_STUDENT_MST converted to VIEW sm_student_mst (PG has no SYNONYM object)
-- SYNONYM SM_SUBJECT_MST converted to VIEW sm_subject_mst (PG has no SYNONYM object)
-- SYNONYM SM_TEACHER_MST converted to VIEW sm_teacher_mst (PG has no SYNONYM object)
-- SYNONYM SM_SCORE_REC converted to VIEW sm_score_rec (PG has no SYNONYM object)
-- SYNONYM SM_SYSTEM_CONFIG converted to VIEW sm_system_config (PG has no SYNONYM object)
-- SYNONYM SM_WK_SCORE_REPORT converted to VIEW sm_wk_score_report (PG has no SYNONYM object)
-- SYNONYM SM_ATTACHMENT_STORAGE converted to VIEW sm_attachment_storage (PG has no SYNONYM object)
-- SYNONYM SM_PROCESS_LOG converted to VIEW sm_process_log (PG has no SYNONYM object)
-- SYNONYM SM_SEQ_STUDENT converted to VIEW sm_seq_student (PG has no SYNONYM object)
-- SYNONYM SM_SEQ_JOBNUM converted to VIEW sm_seq_jobnum (PG has no SYNONYM object)
-- SYNONYM SM_SEQ_LOG converted to VIEW sm_seq_log (PG has no SYNONYM object)

-- ============================================================================
-- 同义词 (Oracle特有 - PostgreSQL不支持，需要用VIEW或SCHEMA)
-- ============================================================================

-- 公有同义词
CREATE OR REPLACE VIEW sm_student_mst AS SELECT * FROM student_mst;
CREATE OR REPLACE VIEW sm_subject_mst AS SELECT * FROM subject_mst;
CREATE OR REPLACE VIEW sm_teacher_mst AS SELECT * FROM teacher_mst;
CREATE OR REPLACE VIEW sm_score_rec AS SELECT * FROM score_rec;
CREATE OR REPLACE VIEW sm_system_config AS SELECT * FROM system_config;
CREATE OR REPLACE VIEW sm_wk_score_report AS SELECT * FROM wk_score_report;
CREATE OR REPLACE VIEW sm_attachment_storage AS SELECT * FROM attachment_storage;
CREATE OR REPLACE VIEW sm_process_log AS SELECT * FROM process_log;

-- 序列同义词
CREATE OR REPLACE VIEW sm_seq_student AS SELECT nextval('seq_student_no') AS next_value;
CREATE OR REPLACE VIEW sm_seq_jobnum AS SELECT nextval('seq_jobnum') AS next_value;
CREATE OR REPLACE VIEW sm_seq_log AS SELECT nextval('seq_log_id') AS next_value;
