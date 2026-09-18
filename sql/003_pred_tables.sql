-- 예측 계층 (data-model.md §2)
-- FR-022: 갱신이 이전 예측을 덮어쓰지 않는다 → run_id 를 PK 에 포함
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS pred_daily (
  target_date          DATE        NOT NULL,
  region_cd            VARCHAR(24) NOT NULL,
  run_id               BIGINT      NOT NULL,
  is_current           TINYINT(1)  NOT NULL DEFAULT 1,
  expected_count       DECIMAL(10,6) NULL,
  occur_probability    DECIMAL(7,6)  NULL,
  occur_prob_raw       DECIMAL(7,6)  NULL,
  risk_grade           ENUM('낮음','보통','높음','매우높음') NULL,
  risk_rank            SMALLINT      NULL,
  spread_probability   DECIMAL(7,6)  NULL,
  longburn_probability DECIMAL(7,6)  NULL,
  expected_damage_load DECIMAL(12,6) NULL,
  top_factors          JSON          NULL,
  confidence           ENUM('정상','대리지점','폴백') NOT NULL DEFAULT '정상',
  data_status          ENUM('정상','최신아님','데이터없음') NOT NULL DEFAULT '정상',
  source_model_version VARCHAR(40)   NULL,
  created_at           DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (target_date, region_cd, run_id),
  KEY ix_daily_rank (target_date, is_current, risk_rank),
  KEY ix_region_date (region_cd, target_date),
  KEY ix_current_grade (is_current, target_date, risk_grade)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS pred_feature_snapshot (
  target_date        DATE        NOT NULL,
  region_cd          VARCHAR(24) NOT NULL,
  run_id             BIGINT      NOT NULL,
  features           JSON        NULL,
  feature_grade      JSON        NULL,
  forecast_issued_at DATETIME    NULL,
  station_id         VARCHAR(16) NULL,
  psi                JSON        NULL,
  PRIMARY KEY (target_date, region_cd, run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS pred_cause_top3 (
  target_date  DATE        NOT NULL,
  region_cd    VARCHAR(24) NOT NULL,
  run_id       BIGINT      NOT NULL,
  `rank`       TINYINT     NOT NULL,
  cause_class  VARCHAR(40) NOT NULL,
  probability  DECIMAL(7,6) NOT NULL,
  lift_vs_base DECIMAL(9,4) NULL,
  PRIMARY KEY (target_date, region_cd, run_id, `rank`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
