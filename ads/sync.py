"""data/ads_plan.json(지금 유형별 가중치) → Google Ads 광고그룹 켜기/끄기 (+선택: 캠페인 예산 배분).

광고그룹 이름 규칙: 이름 끝에 [유형] 태그. 예) 하나_시니어_DG_병원_서울 [병원]
  유형 태그: 노인복지관 노인교실 전통시장 대형병원 병원 요양병원 보건소 등산로 공원 대형점포 라운지
  (화면 이름 '상급·종합병원', '등산로 입구', '주요 공원', '백화점·대형마트', '하나 시니어 라운지'도 인식)
  지역 구분 태그를 같이 써도 됩니다: [병원|서울]
태그가 없는 광고그룹·캠페인은 절대 건드리지 않습니다.

안전장치
- 필수 비밀값이 없거나 저장소 변수 GADS_LIVE 가 '1' 이 아니면 '모의 실행': 바꿀 내용만 data/ads_log.json 에 기록.
- 실제 실행도 먼저 validateOnly 로 검증한 뒤 반영.
- 예산 배분은 GADS_BUDGET_MODE=share 이고 GADS_DAILY_BUDGET_WON(하루 총예산, 원) 이 있을 때만.
"""
import json, os, re, sys, datetime
from gads import Ads, missing

ALIAS = {'상급·종합병원': '대형병원', '등산로 입구': '등산로', '주요 공원': '공원', '백화점·대형마트': '대형점포',
         '하나 시니어 라운지': '라운지', '종합병원': '대형병원', '대형마트': '대형점포', '백화점': '대형점포'}
TAG = re.compile(r'\[([^\]|]+)(?:\|([^\]]+))?\]\s*$')

def tag_of(name, keys):
    m = TAG.search(name or '')
    if not m: return None, None
    k = m.group(1).strip(); k = ALIAS.get(k, k)
    return (k if k in keys else None), (m.group(2) or '').strip() or None

def main():
    plan = json.load(open('data/ads_plan.json'))
    keys = {t['key']: t for t in plan['types']}
    live = os.environ.get('GADS_LIVE', '') == '1'
    miss = missing()
    log = {'time': plan['time'], 'dayType': plan['dayType'], 'mode': 'live' if (live and not miss) else 'dry_run',
           'targets_on': [t['name'] for t in plan['types'] if t['on']], 'changes': [], 'note': ''}
    if miss:
        log['note'] = '비밀값 없음: ' + ', '.join(miss) + ' → 모의 실행(계획만 기록)'
        log['plan'] = {t['key']: {'on': t['on'], 'now': t['now'], 'share': t['share']} for t in plan['types']}
        json.dump(log, open('data/ads_log.json', 'w'), ensure_ascii=False, indent=1); print(log['note']); return

    ads = Ads()
    rows = ads.search("SELECT campaign.id, campaign.name, campaign.status, campaign.campaign_budget, "
                      "ad_group.id, ad_group.name, ad_group.status, ad_group.resource_name "
                      "FROM ad_group WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
    ops, seen, budget_by_type = [], 0, {}
    for r in rows:
        ag, cp = r['adGroup'], r['campaign']
        k, region = tag_of(ag['name'], keys)
        if not k: continue
        seen += 1
        want = 'ENABLED' if keys[k]['on'] else 'PAUSED'
        budget_by_type.setdefault(k, set()).add(cp.get('campaignBudget'))
        if ag['status'] != want:
            ops.append({'update': {'resourceName': ag['resourceName'], 'status': want}, 'updateMask': 'status'})
            log['changes'].append({'adGroup': ag['name'], 'from': ag['status'], 'to': want,
                                   'weight': f"{keys[k]['now']-1:+.1%}"})
    log['adgroups_tagged'] = seen
    if not seen:
        log['note'] = '[유형] 태그가 붙은 광고그룹이 없습니다. 광고그룹 이름 끝에 [병원] 처럼 태그를 붙이세요.'

    # 선택: 캠페인 예산 배분(유형별 캠페인 구조일 때만 의미 있음)
    bops = []
    total = os.environ.get('GADS_DAILY_BUDGET_WON', '').replace(',', '')
    if os.environ.get('GADS_BUDGET_MODE') == 'share' and total.isdigit():
        floor = 0.05  # 꺼진 유형도 최소 5%는 남겨 학습 유지
        on = [k for k in budget_by_type if keys[k]['on']]
        owners = {}
        for kk, v in budget_by_type.items():
            for b in v: owners.setdefault(b, set()).add(kk)
        cand = {k: [b for b in buds if b and len(owners[b]) == 1] for k, buds in budget_by_type.items()}  # 공유 예산 제외
        cand = {k: v for k, v in cand.items() if v}
        w = {k: max(floor, keys[k]['share'] if on else 1 / max(1, len(cand))) for k in cand}
        ws = sum(w.values()) or 1
        for k, buds in cand.items():
            amt = int(total) * w[k] / ws / len(buds)
            for b in buds:
                won = int(round(amt / 10) * 10)
                bops.append({'update': {'resourceName': b, 'amountMicros': str(won * 1_000_000)}, 'updateMask': 'amount_micros'})
                log['changes'].append({'budget': b, 'type': keys[k]['name'], 'won': won})

    if log['mode'] == 'dry_run':
        log['note'] = (log['note'] + ' ' if log['note'] else '') + '저장소 변수 GADS_LIVE=1 이 아니어서 모의 실행'
    else:
        if ops: ads.mutate('adGroups', ops, validate_only=True); ads.mutate('adGroups', ops)
        if bops: ads.mutate('campaignBudgets', bops, validate_only=True); ads.mutate('campaignBudgets', bops)
    json.dump(log, open('data/ads_log.json', 'w'), ensure_ascii=False, indent=1)
    print(log['mode'], '태그 광고그룹', seen, '변경', len(log['changes']), log['note'])

if __name__ == '__main__':
    main()
