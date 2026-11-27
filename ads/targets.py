"""(수동 실행) 태그 광고그룹마다 해당 유형 거점의 반경 1km 위치 타겟을 맞춥니다.
- data/ads_pois.json (plan.py --pois 로 생성) 사용
- 광고그룹 [병원|서울] 처럼 지역 태그가 있으면 주소가 그 지역으로 시작하는 거점만
- 광고그룹의 반경 개수가 목록과 다르면 기존 반경을 지우고 다시 넣음(같으면 건너뜀)
- 캠페인당 반경 500개 한도: 넘으면 그 광고그룹은 건너뛰고 지역을 나누라고 기록
- Demand Gen 은 캠페인에서 '광고그룹 단위 위치 타겟(upgraded targeting)'이 켜져 있어야 함
- 반경을 넣은 광고그룹의 지역 타겟(예: 서울특별시 자리표시자)은 지움 (GADS_KEEP_REGION=1 이면 유지)
- GADS_LIVE=1 이 아니면 모의 실행
"""
import json, os
from gads import Ads, missing
from sync import tag_of

LIMIT = 500
SIDO = {'서울': '서울', '부산': '부산', '대구': '대구', '인천': '인천', '광주': '광주', '대전': '대전', '울산': '울산', '세종': '세종',
        '경기': '경기', '강원': '강원', '충북': '충청북도', '충남': '충청남도', '전북': '전북', '전남': '전라남도',
        '경북': '경상북도', '경남': '경상남도', '제주': '제주'}

def in_region(addr, region):
    if not region: return True
    pre = SIDO.get(region, region)
    a = addr or ''
    return a.startswith(pre) or a.startswith(region)

def main():
    pois = json.load(open('data/ads_pois.json'))
    keys = sorted({p['t'] for p in pois})
    live = os.environ.get('GADS_LIVE', '') == '1'
    log = {'mode': 'dry_run', 'adgroups': [], 'note': ''}
    miss = missing()
    if miss:
        cnt = {}
        for p in pois: cnt[p['t']] = cnt.get(p['t'], 0) + 1
        log['note'] = '비밀값 없음: ' + ', '.join(miss) + ' → 유형별 거점 수만 기록'; log['poi_count'] = cnt
        json.dump(log, open('data/ads_targets_log.json', 'w'), ensure_ascii=False, indent=1); print(log['note'], cnt); return
    ads = Ads()
    ags = ads.search("SELECT campaign.resource_name, ad_group.resource_name, ad_group.name FROM ad_group "
                     "WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
    regions = ads.search("SELECT ad_group.resource_name, ad_group_criterion.resource_name FROM ad_group_criterion "
                         "WHERE ad_group_criterion.type = 'LOCATION' AND ad_group_criterion.negative = FALSE")
    region_of = {}
    for c in regions:
        region_of.setdefault(c['adGroup']['resourceName'], []).append(c['adGroupCriterion']['resourceName'])
    crit = ads.search("SELECT ad_group.resource_name, ad_group_criterion.resource_name FROM ad_group_criterion "
                      "WHERE ad_group_criterion.type = 'PROXIMITY'")
    have = {}  # GAQL에서 반경 좌표는 조회가 안 되므로 개수로 비교: 다르면 지우고 다시 넣음
    for c in crit:
        have.setdefault(c['adGroup']['resourceName'], []).append(c['adGroupCriterion']['resourceName'])
    per_campaign = {}
    plan = []
    for r in ags:
        ag = r['adGroup']; k, region = tag_of(ag['name'], keys)
        if not k: continue
        want = {(round(p['lat'] * 1e6), round(p['lon'] * 1e6)): p for p in pois if p['t'] == k and in_region(p['ad'], region)}
        cp = r['campaign']['resourceName']; per_campaign[cp] = per_campaign.get(cp, 0) + len(want)
        plan.append((cp, ag, want, have.get(ag['resourceName'], {})))
    ops, rm, rm_first = [], [], []
    for cp, ag, want, cur in plan:
        ent = {'adGroup': ag['name'], 'want': len(want), 'have': len(cur)}
        if per_campaign[cp] > LIMIT:
            ent['skip'] = f'이 캠페인 반경 합계 {per_campaign[cp]}개 > {LIMIT}. [유형|지역] 태그로 광고그룹/캠페인을 나누세요.'
            log['adgroups'].append(ent); continue
        if len(cur) == len(want):
            ent['add'] = 0; ent['note'] = '이미 같은 개수 → 건너뜀'
            log['adgroups'].append(ent); continue
        ent['add'] = len(want); ent['remove_old_radius'] = len(cur)
        rm_first.extend({'remove': rn} for rn in cur)
        for la, lo in want:
            ops.append({'create': {'adGroup': ag['resourceName'], 'proximity': {
                'geoPoint': {'latitudeInMicroDegrees': la, 'longitudeInMicroDegrees': lo},
                'radius': 1, 'radiusUnits': 'KILOMETERS'}}})
        # 반경을 넣으면 '서울특별시' 같은 지역 타겟(자리표시자)은 지움 → 반경 안만 남음 (GADS_KEEP_REGION=1 이면 유지)
        if want and os.environ.get('GADS_KEEP_REGION') != '1':
            reg = region_of.get(ag['resourceName'], []); ent['remove_region'] = len(reg)
            rm += [{'remove': rn} for rn in reg]
        log['adgroups'].append(ent)
    if live:
      log['mode'] = 'live'
      try:
        if rm_first: ads.mutate('adGroupCriteria', rm_first)  # 개수가 다른 기존 반경 삭제
        if ops: ads.mutate('adGroupCriteria', ops, validate_only=True); ads.mutate('adGroupCriteria', ops)
        if rm: ads.mutate('adGroupCriteria', rm)  # 반경 추가가 끝난 뒤 지역 타겟 삭제
      except Exception as e:
        log['error'] = str(e)[:2000]
        json.dump(log, open('data/ads_targets_log.json', 'w'), ensure_ascii=False, indent=1)
        raise
    else:
        log['note'] = '저장소 변수 GADS_LIVE=1 이 아니어서 모의 실행'
    json.dump(log, open('data/ads_targets_log.json', 'w'), ensure_ascii=False, indent=1)
    print(log['mode'], json.dumps(log['adgroups'], ensure_ascii=False)[:1500])

if __name__ == '__main__':
    main()
