import { Router } from 'express';
import { q, q1 } from '../db/pool.js';

export const causeRouter = Router();

/** 불합격 모델의 산출임을 화면이 숨기지 못하게, 근거를 구조화해 함께 내보낸다. */
interface CaveatDetail {
  headline: string;
  lead: string;
  reasons: string[];
  warning: string;
  trustworthy: string;
}

/** M3 는 홀드아웃에서 합격 기준을 넘지 못했다.
 *  top-3 적중률 0.9169 는 최빈 3클래스를 고정 제시하는 상수 예측기(0.9170)와 사실상 동률이다.
 *  화면이 이 사실을 숨기면 사용자가 순위를 근거로 대응 장비를 고르게 된다 — 반드시 함께 내보낸다. */
async function m3Status() {
  const r = await q1<any>(
    `SELECT model_version, acceptance_passed, holdout_metrics
       FROM ops_model_registry WHERE model_id='M3' AND is_active=1 LIMIT 1`);
  if (!r) return { loaded: false, passed: false, caveat: null as string | null, metrics: null,
                   reliability: '미적재', caveatDetail: null as CaveatDetail | null };
  const m = typeof r.holdout_metrics === 'string' ? JSON.parse(r.holdout_metrics) : r.holdout_metrics;
  const passed = !!r.acceptance_passed;

  const p1 = (x: number) => (x * 100).toFixed(2);
  const top3 = Number(m?.top3);
  const base = Number(m?.const_top3_baseline);
  const lift = Number(m?.minority_lift);
  const target = m?.lift_target ?? '방화';

  // 신호가 실재하는 클래스만 골라낸다 — 이것만이 이 모델에서 믿을 수 있는 부분이다.
  const useful = Object.entries(m ?? {})
    .filter(([k, v]) => k.startsWith('lift_') && typeof v === 'number' && (v as number) >= 2.0)
    .map(([k, v]) => `${k.slice(5)} ${(v as number).toFixed(2)}배`)
    .sort();

  // 안내는 두 줄로 끝낸다. 길면 아무도 안 읽고, 안 읽히는 경고는 없는 것과 같다.
  const reasons: string[] = [];

  return {
    loaded: true,
    passed,
    modelVersion: r.model_version,
    metrics: m,
    // 화면이 위험도를 낮춰 보이게 하지 않도록 등급을 명시적으로 내보낸다.
    // 순위를 빼고 기준(2.0배)을 넘은 신호만 내보내므로 '불안전'이 아니라 '참고용'이다.
    // 무가치한 값을 보여주며 변명하는 상태가 아니라, 유효한 부분만 남긴 상태다.
    reliability: passed ? '정상' : '참고용',
    caveatDetail: passed ? null : {
      headline: '참고용입니다',
      lead: '평소보다 2배 넘게 늘어난 원인만 보여드립니다. 부주의·전기처럼 늘 많은 원인은 '
            + '여기 뜨지 않는 것이 정상입니다.',
      reasons,
      warning: '아직 정확도가 충분하지 않으니 이것만 보고 배치를 결정하지 마세요.',
      trustworthy: '',
    },
    caveat: passed ? null
      : '평소보다 2배 넘게 늘어난 원인만 보여드립니다. 부주의·전기처럼 늘 많은 원인은 '
        + '여기 뜨지 않는 것이 정상입니다. '
        + '아직 정확도가 충분하지 않으니 이것만 보고 배치를 결정하지 마세요.',
  };
}

/** 모델이 실제로 신호를 내는 원인 유형 — 홀드아웃 클래스별 lift */
causeRouter.get('/_signal', async (_req, res, next) => {
  try {
    const s = await m3Status();
    const m: any = s.metrics ?? {};
    const lifts = Object.entries(m)
      .filter(([k, v]) => k.startsWith('lift_') && typeof v === 'number')
      .map(([k, v]) => ({ causeClass: k.slice(5), lift: v as number,
                          useful: (v as number) >= 2.0 }))
      .sort((a, b) => b.lift - a.lift);
    res.json({ ...s, lifts });
  } catch (e) { next(e); }
});

/** 평시 대비 배율이 이 값을 넘은 원인만 '이상 신호' 로 내보낸다.
 *  M3 의 합격 기준과 같은 값이다 — 기준을 넘은 부분만 화면에 남긴다. */
const ANOMALY_LIFT = 2.0;

/** FR-013 · US3 — **원인 이상 신호**.
 *  순위(어느 원인이 1위인가)는 내보내지 않는다. M3 는 그 부분에서 상수 예측기에
 *  졌으므로 정보가 없고, 보여주면 사용자가 근거로 삼는다. 평시 대비 배율이
 *  기준을 넘은 것만 남긴다 — 이쪽은 홀드아웃에서 실제로 신호가 확인됐다. */
causeRouter.get('/:regionCd/:date', async (req, res, next) => {
  try {
    const [rows, status] = await Promise.all([
      q(`SELECT \`rank\`, cause_class AS causeClass, probability, lift_vs_base AS liftVsBase
           FROM pred_cause_top3
          WHERE region_cd=? AND target_date=?
          ORDER BY lift_vs_base DESC`,
        [decodeURIComponent(req.params.regionCd), req.params.date]),
      m3Status(),
    ]);
    if (!rows.length) {
      return res.json({
        items: [], model: status, threshold: ANOMALY_LIFT, scanned: 0,
        unavailable: status.loaded
          ? '이 날짜는 날씨 정보가 없어 지역·계절 평균값으로만 계산했습니다. 그래서 원인 분석이 없습니다.'
          : '원인 분석 기능이 아직 준비되지 않았습니다.',
      });
    }
    const items = rows
      .filter((r: any) => r.liftVsBase != null && Number(r.liftVsBase) >= ANOMALY_LIFT)
      .map((r: any) => ({ ...r, anomaly: true }));

    res.json({
      items, model: status, threshold: ANOMALY_LIFT, scanned: rows.length,
      // 신호가 없는 것은 오류가 아니라 정상이다 — 화면이 그렇게 말하게 한다
      unavailable: items.length ? null
        : '오늘 이 지역은 평소와 다른 점이 눈에 띄지 않습니다.',
    });
  } catch (e) { next(e); }
});

