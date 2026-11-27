"""정시마다 '지금 가중치가 붙은 거점'의 반경 1km 를 Google Ads 캠페인 위치 타겟으로 바꿔 넣습니다.

대상: 광고그룹 이름 끝이 [전체] 또는 [전체|지역] 인 광고그룹이 들어 있는 캠페인
      (Demand Gen 은 반경을 캠페인 단위로만 넣을 수 있음 → 캠페인 설정에서 '캠페인 위치 및 언어 설정 사용' 켜기)
동작:
 1) data/ads_plan.json 에서 지금 켜진 유형(+3% 이상)을 고르고
 2) data/ads_pois.json 의 그 유형 거점을 반경 1km 예측 5064 인구(sc) 큰 순으로 최대 GADS_MAX_RADIUS 개(기본 200, 한도 500)
 3) 캠페인의 기존 반경과 비교해 빠진 것은 삭제, 새로 들어온 것만 추가(주소를 반경 이름으로 붙임)
 4) 반경이 생기면 '서울특별시' 같은 지역 자리표시자는 삭제
 - 켜진 유형이 하나도 없으면 반경을 비우지 않고 그대로 둡니다(위치 0개 = 전국 노출이 되므로).
 - API 사용량: Explorer 접근은 하루 2,880 작업 한도 → 바뀐 것만 보내고, 검증 호출(validateOnly)은 생략합니다.
 - GADS_LIVE=1 이 아니면 모의 실행. 결과는 data/ads_radius_log.json
"""
import json, os, re
from gads import Ads, missing

TAG = re.compile(r'\[\s*전체\s*(?:\|([^\]]+))?\]\s*$')
SIDO = {'충북': '충청북도', '충남': '충청남도', '전남': '전라남도', '경북': '경상북도', '경남': '경상남도'}


def in_region(addr, region):
    if not region: return True
    a = addr or ''
    return a.startswith(region) or a.startswith(SIDO.get(region, region))


def save(log):
    json.dump(log, open('data/ads_radius_log.json', 'w'), ensure_ascii=False, indent=1)


def main():
    plan = json.load(open('data/ads_plan.json'))
    pois = json.load(open('data/ads_pois.json'))
    cap = min(500, int(os.environ.get('GADS_MAX_RADIUS') or 200))
    live = os.environ.get('GADS_LIVE', '') == '1'
    on = [t['key'] for t in plan['types'] if t['on']]
    log = {'time': plan['time'], 'mode': 'dry_run', 'types_on': [t['name'] for t in plan['types'] if t['on']],
           'cap': cap, 'campaigns': [], 'note': ''}
    if missing():
        log['note'] = '비밀값 없음 → 모의 실행'; save(log); print(log['note']); return
    ads = Ads()
    rows = ads.search("SELECT campaign.resource_name, campaign.name, ad_group.name FROM ad_group "
                      "WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
    camp = {}
    for r in rows:
        m = TAG.search(r['adGroup']['name'] or '')
        if m: camp.setdefault(r['campaign']['resourceName'], {'name': r['campaign']['name'], 'region': (m.group(1) or '').strip() or None})
    if not camp:
        log['note'] = '[전체] 태그 광고그룹이 있는 캠페인이 없습니다.'; save(log); print(log['note']); return
    # 기존 반경(좌표) · 지역 타겟
    cur, coords_ok = {}, True
    try:
        crit = ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name, "
                          "campaign_criterion.proximity.geo_point.latitude_in_micro_degrees, "
                          "campaign_criterion.proximity.geo_point.longitude_in_micro_degrees "
                          "FROM campaign_criterion WHERE campaign_criterion.type = 'PROXIMITY' AND campaign_criterion.negative = FALSE")
        for c in crit:
            gp = c['campaignCriterion'].get('proximity', {}).get('geoPoint', {})
            key = (int(gp.get('latitudeInMicroDegrees', 0)), int(gp.get('longitudeInMicroDegrees', 0)))
            cur.setdefault(c['campaign']['resourceName'], {})[key] = c['campaignCriterion']['resourceName']
    except RuntimeError:
        coords_ok = False  # 좌표 조회가 안 되면 전부 지우고 다시 넣음
        crit = ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name FROM campaign_criterion "
                          "WHERE campaign_criterion.type = 'PROXIMITY' AND campaign_criterion.negative = FALSE")
        for i, c in enumerate(crit):
            cur.setdefault(c['campaign']['resourceName'], {})[('x', i)] = c['campaignCriterion']['resourceName']
    loc = {}
    for c in ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name FROM campaign_criterion "
                        "WHERE campaign_criterion.type = 'LOCATION' AND campaign_criterion.negative = FALSE"):
        loc.setdefault(c['campaign']['resourceName'], []).append(c['campaignCriterion']['resourceName'])

    rm, add, rm_loc = [], [], []
    for cp, c in camp.items():
        cand = [p for p in pois if p['t'] in on and in_region(p['ad'], c['region'])]
        cand.sort(key=lambda p: -p.get('sc', 0))
        want = {}
        for p in cand:
            k = (round(p['lat'] * 1e6), round(p['lon'] * 1e6))
            if k not in want: want[k] = p
            if len(want) >= cap: break
        have = cur.get(cp, {})
        ent = {'campaign': c['name'], 'region': c['region'] or '전국', 'candidates': len(cand), 'want': len(want), 'have': len(have)}
        if not want:
            ent['note'] = '지금 켜진 유형이 없어 기존 반경 유지'; log['campaigns'].append(ent); continue
        gone = [rn for k, rn in have.items() if k not in want]
        new = [k for k in want if k not in have]
        ent['remove'] = len(gone); ent['add'] = len(new)
        ent['top'] = [f"{want[k]['nm']} ({want[k]['t']}, {want[k].get('sc', 0):,}명)" for k in list(want)[:10]]
        rm += [{'remove': rn} for rn in gone]
        for k in new:
            p = want[k]
            add.append({'create': {'campaign': cp, 'proximity': {
                'geoPoint': {'latitudeInMicroDegrees': k[0], 'longitudeInMicroDegrees': k[1]},
                'radius': 1, 'radiusUnits': 'KILOMETERS',
                'address': {'streetAddress': (p.get('ad') or p['nm'])[:80], 'countryCode': 'KR'}}}})
        if loc.get(cp):
            ent['remove_region'] = len(loc[cp]); rm_loc += [{'remove': rn} for rn in loc[cp]]
        log['campaigns'].append(ent)
    log['operations'] = len(rm) + len(add) + len(rm_loc)
    if not coords_ok: log['note'] = '반경 좌표 조회 불가 → 전체 교체 방식'
    if live:
        log['mode'] = 'live'
        try:
            if add: ads.mutate('campaignCriteria', add)       # 먼저 추가(위치가 0개가 되는 순간이 없도록)
            if rm: ads.mutate('campaignCriteria', rm)
            if rm_loc: ads.mutate('campaignCriteria', rm_loc)
        except Exception as e:
            log['error'] = str(e)[:2000]; save(log); raise
    save(log)
    print(log['mode'], json.dumps(log['campaigns'], ensure_ascii=False)[:1200])


if __name__ == '__main__':
    main()
