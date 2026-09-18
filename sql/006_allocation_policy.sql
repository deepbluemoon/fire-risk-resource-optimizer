-- 인력 배분안 — 정책값과 성과 지표를 함께 남긴다 (2026-09-04)
--
-- 왜 필요한가:
--   배분안은 이제 정책값 α(최소 유지 비율) · β(배치 상한 배수) · δ(일일 조정 상한)
--   에 따라 답이 달라진다. 같은 날 같은 예측이어도 α 를 0.5 로 뒀는지 0.9 로 뒀는지에
--   따라 권장 인원이 완전히 달라지므로, 그 값을 함께 남기지 않으면 나중에
--   "왜 이런 안이 나왔나" 를 되짚을 수 없다. FR-039(이력 보존)의 실질 요건이다.
--
--   moved_total 은 그 안을 실행할 때 실제로 옮겨야 하는 인원이다. 미충족만 남기면
--   "개선 79%" 같은 숫자가 31개 지역을 0명으로 비우는 안이어도 좋아 보인다.
--   비용을 함께 적어야 지표가 정직해진다.

ALTER TABLE ops_allocation_proposal
  ADD COLUMN policy       JSON         NULL COMMENT '{alpha,beta,delta,c} 산출에 쓴 정책값' AFTER mode,
  ADD COLUMN moved_total  DECIMAL(14,4) NULL COMMENT '권장안 실행 시 이동 인원(편도)'      AFTER baseline_unmet_risk,
  ADD COLUMN staff_source VARCHAR(32)  NULL COMMENT '현재 인력 N 의 출처'                 AFTER moved_total;

ALTER TABLE ops_allocation_proposal_item
  ADD COLUMN need_headcount  DECIMAL(12,4) NULL COMMENT '위험 비례 목표 need(r)'   AFTER recommended_ratio,
  ADD COLUMN current_staff   DECIMAL(12,4) NULL COMMENT '현재 인력 N(r)'           AFTER need_headcount;

-- recommended_headcount 는 int 라 반올림 손실이 생긴다. 총량 보존을 검산하려면
-- 소수 그대로가 필요하다.
ALTER TABLE ops_allocation_proposal_item
  MODIFY COLUMN recommended_headcount DECIMAL(12,4) NULL COMMENT '권장 배치 x(r)';
