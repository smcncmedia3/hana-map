"""(수동 실행) 캠페인마다 반경 1km 위치 타겟을 맞춥니다.

Demand Gen 은 광고그룹 단위로 반경을 넣을 수 없어서 '캠페인 단위 위치'를 씁니다.
 → 유형마다 캠페인을 따로 만들고(캠페인 위치 및 언어 설정 사용 = 켬), 그 안 광고그룹 이름 끝에 [유형] 태그.
- data/ads_pois.json (plan.py --pois 로 생성) 사용
- 캠페인 안 태그 광고그룹들의 유형(+[유형|지역] 지역 태그) 거점을 모아 그 캠페인의 반경 목록으로 씀
- 캠페인의 반경 개수가 목록과 다르면 기존 반경을 지우고 다시 넣음(같으면 건너뜀)
- 캠페인당 반경 500개 한도: 넘으면 건너뛰고 지역을 나누라고 기록
- 반경을 넣은 캠페인의 지역 타겟(예: 서울특별시 자리표시자)은 지움 (GADS_KEEP_REGION=1 이면 유지)
- 캠페인 단위 위치를 쓰지 않는 캠페인(광고그룹 단위 위치)은 건너뜀
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


def save(log):
    json.dump(log, open('data/ads_targets_log.json', 'w'), ensure_ascii=False, indent=1)


def main():
    pois = json.load(open('data/ads_pois.json'))
    keys = sorted({p['t'] for p in pois})
    live = os.environ.get('GADS_LIVE', '') == '1'
    log = {'mode': 'dry_run', 'campaigns': [], 'note': ''}
    miss = missing()
    if miss:
        cnt = {}
        for p in pois: cnt[p['t']] = cnt.get(p['t'], 0) + 1
        log['note'] = '비밀값 없음: ' + ', '.join(miss) + ' → 유형별 거점 수만 기록'; log['poi_count'] = cnt
        save(log); print(log['note'], cnt); return
    ads = Ads()
    ags = ads.search("SELECT campaign.resource_name, campaign.name, ad_group.name FROM ad_group "
                     "WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
    camp = {}
    for r in ags:
        k, region = tag_of(r['adGroup']['name'], keys)
        if not k: continue
        c = camp.setdefault(r['campaign']['resourceName'], {'name': r['campaign']['name'], 'tags': set()})
        c['tags'].add((k, region))
    crit = ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name, campaign_criterion.type "
                      "FROM campaign_criterion WHERE campaign_criterion.type IN ('PROXIMITY','LOCATION') "
                      "AND campaign_criterion.negative = FALSE")
    prox, loc = {}, {}
    for c in crit:
        d = prox if c['campaignCriterion']['type'] == 'PROXIMITY' else loc
        d.setdefault(c['campaign']['resourceName'], []).append(c['campaignCriterion']['resourceName'])
    rm_first, ops, rm = [], [], []
    for cp, c in camp.items():
        want = {}
        for k, region in c['tags']:
            for p in pois:
                if p['t'] == k and in_region(p['ad'], region):
                    want[(round(p['lat'] * 1e6), round(p['lon'] * 1e6))] = 1
        cur = prox.get(cp, [])
        ent = {'campaign': c['name'], 'types': sorted(f'{k}|{r}' if r else k for k, r in c['tags']),
               'want': len(want), 'have': len(cur)}
        if cp not in loc and cp not in prox:
            ent['skip'] = '캠페인 단위 위치가 없음(광고그룹 단위 위치 캠페인일 수 있음) → 건너뜀'
            log['campaigns'].append(ent); continue
        if len(want) > LIMIT:
            ent['skip'] = f'반경 {len(want)}개 > {LIMIT}. [유형|지역] 태그로 캠페인을 나누세요.'
            log['campaigns'].append(ent); continue
        if len(cur) == len(want):
            ent['note'] = '이미 같은 개수 → 건너뜀'
        else:
            ent['add'] = len(want); ent['remove_old_radius'] = len(cur)
            rm_first += [{'remove': rn} for rn in cur]
            ops += [{'create': {'campaign': cp, 'proximity': {
                'geoPoint': {'latitudeInMicroDegrees': la, 'longitudeInMicroDegrees': lo},
                'radius': 1, 'radiusUnits': 'KILOMETERS'}}} for la, lo in want]
        if want and loc.get(cp) and os.environ.get('GADS_KEEP_REGION') != '1':
            ent['remove_region'] = len(loc[cp]); rm += [{'remove': rn} for rn in loc[cp]]
        log['campaigns'].append(ent)
    if not camp:
        log['note'] = '[유형] 태그가 붙은 광고그룹이 없습니다.'
    if live:
        log['mode'] = 'live'
        try:
            if rm_first: ads.mutate('campaignCriteria', rm_first)
            if ops: ads.mutate('campaignCriteria', ops, validate_only=True); ads.mutate('campaignCriteria', ops)
            if rm: ads.mutate('campaignCriteria', rm)  # 반경을 넣은 뒤 지역 자리표시자 삭제
        except Exception as e:
            log['error'] = str(e)[:2000]; save(log); raise
    else:
        log['note'] = (log['note'] + ' ' if log['note'] else '') + '저장소 변수 GADS_LIVE=1 이 아니어서 모의 실행'
    save(log)
    print(log['mode'], json.dumps(log['campaigns'], ensure_ascii=False)[:1500])


if __name__ == '__main__':
    main()
