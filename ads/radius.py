"""정시마다 '지금 가중치가 붙은 거점'의 반경 1km 를 Google Ads 캠페인 위치로 바꿔 넣고, 시간대 지수로 예산을 조절합니다.

대상: 광고그룹 이름 끝이 [전체] 인 광고그룹이 들어 있는 캠페인(여러 개 가능)
      Demand Gen 은 반경을 캠페인 단위로만 넣을 수 있어 '캠페인 위치 및 언어 설정 사용'을 켠 캠페인이어야 함
반경:
 1) data/ads_plan.json 에서 지금 켜진 유형(+3% 이상)을 고르고
 2) data/ads_pois.json 의 그 유형 거점을 반경 1km 예측 5064(sc) 큰 순으로 정렬
 3) 캠페인 이름 순으로 캠페인마다 최대 GADS_MAX_RADIUS 개(기본 250)씩 나눠 담음
    (공식 한도는 500이지만 주소 붙은 반경은 1개가 2칸으로 계산되어 실제로는 250개)
    (캠페인 수 × 500 보다 많으면 순위 아래쪽은 이번 시간 제외 → 로그에 'dropped')
 4) 캠페인별 기존 반경과 비교해 빠진 것은 삭제, 새로 들어온 것만 추가
    반경 이름 = 주소(시도·시군구·도로명 나눠서 넣고 국가 KR)
 5) 반경이 생기면 '서울특별시' 같은 지역 자리표시자 삭제
 - 켜진 유형이 하나도 없으면 반경은 그대로 둠(위치 0개 = 전국 노출 방지)
예산(GADS_DAILY_BUDGET_WON 이 있을 때):
 - 지금 시간 지수(index_now = 전체 반경 인구 상대값 × 유튜브 시간대 이용 지수, 하루 평균 1)를 곱한 하루 예산을
   캠페인별 반경 인구 합 비율로 나눠 캠페인 예산을 바꿈. 지수는 GADS_INDEX_MIN~MAX(기본 0.2~2.0)로 제한
 - 여러 캠페인이 함께 쓰는 공유 예산은 건드리지 않음
기타: Explorer 접근은 실제 계정에 하루 2,880 작업 한도 → 바뀐 것만 보내고 validateOnly 는 생략
      GADS_LIVE=1 이 아니면 모의 실행. 결과는 data/ads_radius_log.json
"""
import json, os, re
from gads import Ads, missing

TAG = re.compile(r'\[\s*전체\s*(?:\|[^\]]*)?\]\s*$')


def addr_info(ad, name):
    """'서울특별시 강동구 양재대로 1580, (명일동)' → 시도/시군구/도로명 으로 나눔"""
    a = (ad or '').split(',')[0].strip() or name
    tk = a.split()
    prov = tk[0] if tk else ''
    city, rest = '', tk[1:]
    if rest and rest[0].endswith(('시', '군', '구')) and not prov.startswith('세종'):
        city = rest[0]; rest = rest[1:]
        if city.endswith('시') and rest and rest[0].endswith(('구', '군')):
            city = city + ' ' + rest[0]; rest = rest[1:]
    street = ' '.join(rest) or name
    if not city: city = prov
    info = {'streetAddress': street[:80], 'cityName': city[:40], 'provinceName': prov[:40], 'countryCode': 'KR'}
    return {k: v for k, v in info.items() if v}


def save(log):
    json.dump(log, open('data/ads_radius_log.json', 'w'), ensure_ascii=False, indent=1)


def main():
    plan = json.load(open('data/ads_plan.json'))
    pois = json.load(open('data/ads_pois.json'))
    cap = min(500, int(os.environ.get('GADS_MAX_RADIUS') or 250))  # 실측: 반경 1개가 한도 2칸을 차지 → 캠페인당 250개
    live = os.environ.get('GADS_LIVE', '') == '1'
    on = [t['key'] for t in plan['types'] if t['on']]
    idx = plan.get('index_now') or 1.0
    lo, hi = float(os.environ.get('GADS_INDEX_MIN') or 0.2), float(os.environ.get('GADS_INDEX_MAX') or 2.0)
    log = {'time': plan['time'], 'mode': 'dry_run', 'types_on': [t['name'] for t in plan['types'] if t['on']],
           'reach_now': plan.get('reach_now'), 'index_now': idx, 'cap_per_campaign': cap, 'campaigns': [], 'note': ''}
    if missing():
        log['note'] = '비밀값 없음 → 모의 실행'; save(log); print(log['note']); return
    ads = Ads()
    rows = ads.search("SELECT campaign.resource_name, campaign.name, campaign.campaign_budget, ad_group.name FROM ad_group "
                      "WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
    camp = {}
    for r in rows:
        if TAG.search(r['adGroup']['name'] or ''):
            camp.setdefault(r['campaign']['resourceName'], {'name': r['campaign']['name'], 'budget': r['campaign'].get('campaignBudget')})
    if not camp:
        log['note'] = '[전체] 태그 광고그룹이 있는 캠페인이 없습니다.'; save(log); print(log['note']); return
    order = sorted(camp, key=lambda cp: camp[cp]['name'])

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
        coords_ok = False
        crit = ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name FROM campaign_criterion "
                          "WHERE campaign_criterion.type = 'PROXIMITY' AND campaign_criterion.negative = FALSE")
        for i, c in enumerate(crit):
            cur.setdefault(c['campaign']['resourceName'], {})[('x', i)] = c['campaignCriterion']['resourceName']
    loc = {}
    for c in ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name FROM campaign_criterion "
                        "WHERE campaign_criterion.type = 'LOCATION' AND campaign_criterion.negative = FALSE"):
        loc.setdefault(c['campaign']['resourceName'], []).append(c['campaignCriterion']['resourceName'])

    # 지금 켜진 유형 거점(좌표 중복 제거) → 인구 순
    cand, seen = [], set()
    for p in sorted((p for p in pois if p['t'] in on), key=lambda p: -p.get('sc', 0)):
        k = (round(p['lat'] * 1e6), round(p['lon'] * 1e6))
        if k in seen: continue
        seen.add(k); cand.append((k, p))
    log['candidates'] = len(cand)
    log['dropped'] = max(0, len(cand) - cap * len(order))

    rm, add, rm_loc, weight = [], [], [], {}
    over = False
    for i, cp in enumerate(order):
        chunk = cand[i * cap:(i + 1) * cap]
        want = dict(chunk)
        have = cur.get(cp, {})
        ent = {'campaign': camp[cp]['name'], 'want': len(want), 'have': len(have)}
        weight[cp] = sum(p.get('sc', 0) for _, p in chunk)
        if not on:
            ent['note'] = '지금 켜진 유형이 없어 기존 반경 유지'; log['campaigns'].append(ent); continue
        if not want:
            ent['note'] = '이번 시간 배정된 거점 없음 → 기존 반경 유지'; log['campaigns'].append(ent); continue
        gone = [rn for k, rn in have.items() if k not in want]
        new = [k for k in want if k not in have]
        ent['remove'] = len(gone); ent['add'] = len(new); ent['people'] = weight[cp]
        if len(have) + len(new) > cap: over = True
        ent['top'] = [f"{want[k]['nm']} ({want[k]['t']}, {want[k].get('sc', 0):,}명)" for k in list(want)[:5]]
        rm += [{'remove': rn} for rn in gone]
        for k in new:
            p = want[k]
            add.append({'create': {'campaign': cp, 'proximity': {
                'geoPoint': {'latitudeInMicroDegrees': k[0], 'longitudeInMicroDegrees': k[1]},
                'radius': 1, 'radiusUnits': 'KILOMETERS', 'address': addr_info(p.get('ad'), p['nm'])}}})
        if loc.get(cp):
            ent['remove_region'] = len(loc[cp]); rm_loc += [{'remove': rn} for rn in loc[cp]]
        log['campaigns'].append(ent)

    # 예산
    bud_ops = []
    total = (os.environ.get('GADS_DAILY_BUDGET_WON') or '').replace(',', '')
    if total.isdigit():
        f = max(lo, min(hi, idx))
        day = int(total) * f
        owners = {}
        for cp in order: owners.setdefault(camp[cp]['budget'], []).append(cp)
        ws = sum(weight.values()) or 1
        log['budget'] = {'base_won': int(total), 'factor': round(f, 3), 'today_rate_won': int(day), 'campaigns': []}
        for cp in order:
            b = camp[cp]['budget']
            if not b or len(owners[b]) > 1: continue
            won = int(round(day * (weight[cp] / ws if ws > 1 else 1 / len(order)) / 10) * 10)
            won = max(won, 1000)
            bud_ops.append({'update': {'resourceName': b, 'amountMicros': str(won * 1_000_000)}, 'updateMask': 'amount_micros'})
            log['budget']['campaigns'].append({'campaign': camp[cp]['name'], 'won': won})

    log['operations'] = len(rm) + len(add) + len(rm_loc) + len(bud_ops)
    if not coords_ok: log['note'] = '반경 좌표 조회 불가 → 전체 교체 방식'
    if live:
        log['mode'] = 'live'
        try:
            eu = ads.search("SELECT campaign.resource_name, campaign.contains_eu_political_advertising FROM campaign "
                            "WHERE campaign.status != 'REMOVED'")
            fix = [{'update': {'resourceName': r['campaign']['resourceName'],
                               'containsEuPoliticalAdvertising': 'DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING'},
                    'updateMask': 'contains_eu_political_advertising'}
                   for r in eu if r['campaign']['resourceName'] in camp
                   and r['campaign'].get('containsEuPoliticalAdvertising') not in ('DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING', 'CONTAINS_EU_POLITICAL_ADVERTISING')]
            if fix: ads.mutate('campaigns', fix); log['eu_declared'] = len(fix)
            if over:   # 한도 초과 우려 → 빠질 반경 먼저 삭제(want 가 비지 않으므로 위치 0개는 아님)
                if rm: ads.mutate('campaignCriteria', rm)
                if add: ads.mutate('campaignCriteria', add)
            else:      # 먼저 추가(위치 0개 순간 방지)
                if add: ads.mutate('campaignCriteria', add)
                if rm: ads.mutate('campaignCriteria', rm)
            if rm_loc: ads.mutate('campaignCriteria', rm_loc)
            if bud_ops: ads.mutate('campaignBudgets', bud_ops)
        except Exception as e:
            log['error'] = str(e)[:2000]; save(log); raise
    save(log)
    print(log['mode'], log['operations'], json.dumps(log['campaigns'], ensure_ascii=False)[:1200])


if __name__ == '__main__':
    main()
