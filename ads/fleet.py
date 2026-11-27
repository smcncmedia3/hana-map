"""하나맵 캠페인 묶음(fleet) — 반경 누락 없이 전국 거점 전부를 Google Ads 에 올리고, 매시간 켜기/끄기·예산만 바꿉니다.

구조 (Basic 접근 = 하루 15,000 작업 전제)
 - 유형마다 캠페인 '[하나맵] 병원 #1', '#2' … 를 만들고 캠페인당 반경 1km 를 최대 GADS_MAX_RADIUS(기본 250)개씩 넣음
   (공식 한도 500이지만 주소 붙은 반경은 1개가 2칸으로 계산됨 — 2026-11-27 실측)
 - 사이트의 문 연 거점 전부(data/ads_pois.json)를 넣음 → 누락 0. 거점이 늘거나 줄면 그 차이만 추가·삭제
   이미 들어간 반경은 다른 캠페인으로 옮기지 않음(작업 수 최소화)
 - 캠페인은 템플릿 캠페인(GADS_TEMPLATE_CAMPAIGN, 기본 '[TEST] 하나_라운지_반경1km')을 그대로 복제:
   입찰·예산 방식, 위치 옵션, 언어, 광고그룹의 연령 타겟, 광고(이미지·문구·URL)
 - 매시간: 지금 켜진 유형(+3% 이상)의 광고그룹은 사용, 나머지는 일시중지
   GADS_SERVE=1 이면 광고그룹 대신 캠페인 자체를 켜고 끔(실제 집행. 반경이 1개도 없는 캠페인은 절대 켜지 않음)
 - 예산: GADS_DAILY_BUDGET_WON × 시간 지수(index_now, GADS_INDEX_MIN~MAX) 를
   켜진 유형의 비중(share) → 그 유형 캠페인들의 반경 인구 합 비율로 나눔. 5% 넘게 달라질 때만 바꿈
작업 한도: data/ads_ops.json 에 미국 태평양시간 날짜별 사용량을 적고 GADS_DAILY_OPS(기본 14,000) 를 넘기지 않음
   순서 = ① 매시간 켜기/끄기·예산 ② 새 캠페인 만들기 ③ 반경 채우기. 남은 반경은 다음 시간에 이어서
모드: fleet.py            → 실행
      fleet.py validate   → 템플릿을 읽고 캠페인 1개 생성을 validateOnly 로 시험(아무것도 바뀌지 않음)
기록: data/ads_fleet_log.json
"""
import json, math, os, sys, re, datetime
from gads import Ads, missing, _post

PREFIX = '[하나맵]'
AGTAG = re.compile(r'^\[하나맵\|([^|\]]+)\|(\d+)\]')
OPS_FILE = 'data/ads_ops.json'
LOG_FILE = 'data/ads_fleet_log.json'


def env(k, d=''):
    return (os.environ.get(k) or d).strip()


def today():
    # Google Ads API 일일 한도는 미국 태평양 시간 자정에 초기화
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d')


class Ops:
    def __init__(self):
        self.limit = int(env('GADS_DAILY_OPS', '14000'))
        try:
            d = json.load(open(OPS_FILE))
        except Exception:
            d = {}
        self.day = today()
        self.used = d.get('used', 0) if d.get('date') == self.day else int(env('GADS_OPS_CARRY', '0') or 0)

    def left(self):
        return max(0, self.limit - self.used)

    def spend(self, n):
        self.used += n
        json.dump({'date': self.day, 'used': self.used, 'limit': self.limit}, open(OPS_FILE, 'w'))


def addr_info(ad, name):
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


def tryq(ads, log, label, q):
    try:
        return ads.search(q)
    except RuntimeError as e:
        log.setdefault('query_errors', {})[label] = str(e)[:600]
        return None


# ---------------------------------------------------------------- 템플릿 읽기
def read_template(ads, log):
    name = env('GADS_TEMPLATE_CAMPAIGN', '[TEST] 하나_라운지_반경1km')
    base = ("campaign.resource_name, campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, "
            "campaign.geo_target_type_setting.positive_geo_target_type, campaign.geo_target_type_setting.negative_geo_target_type, "
            "campaign_budget.amount_micros, campaign_budget.delivery_method")
    extra = ['campaign.target_spend.cpc_bid_ceiling_micros', 'campaign.maximize_conversions.target_cpa_micros',
             'campaign.target_cpa.target_cpa_micros', 'campaign.maximize_conversion_value.target_roas',
             'campaign.demand_gen_campaign_settings.upgraded_targeting']
    where = f" FROM campaign WHERE campaign.name = '{name}' AND campaign.status != 'REMOVED'"
    rows = tryq(ads, log, 'campaign', 'SELECT ' + base + where)
    if not rows:
        raise SystemExit(f'템플릿 캠페인 "{name}" 을 찾지 못했습니다.')
    t = {'campaign': rows[0]['campaign'], 'budget': rows[0].get('campaignBudget', {})}
    for f in extra:   # 계정·버전에 따라 없는 필드가 있어 하나씩
        r = tryq(ads, log, f, f'SELECT campaign.resource_name, {f}' + where)
        if r: deep_merge(t['campaign'], r[0]['campaign'])
    crn = t['campaign']['resourceName']
    lang = tryq(ads, log, 'language', "SELECT campaign_criterion.language.language_constant FROM campaign_criterion "
                f"WHERE campaign.resource_name = '{crn}' AND campaign_criterion.type = 'LANGUAGE'") or []
    t['languages'] = [r['campaignCriterion']['language']['languageConstant'] for r in lang
                      if r.get('campaignCriterion', {}).get('language')]
    ag = tryq(ads, log, 'ad_group', "SELECT ad_group.resource_name, ad_group.name, ad_group.type FROM ad_group "
              f"WHERE campaign.resource_name = '{crn}' AND ad_group.status != 'REMOVED'") or []
    if not ag:
        raise SystemExit('템플릿 캠페인에 광고그룹이 없습니다.')
    t['ad_group'] = ag[0]['adGroup']
    agrn = t['ad_group']['resourceName']
    crit = tryq(ads, log, 'ag_criteria', "SELECT ad_group_criterion.type, ad_group_criterion.negative, ad_group_criterion.age_range.type, "
                "ad_group_criterion.gender.type, ad_group_criterion.parental_status.type, ad_group_criterion.income_range.type "
                f"FROM ad_group_criterion WHERE ad_group.resource_name = '{agrn}' AND ad_group_criterion.status != 'REMOVED'") or []
    t['ag_criteria'] = [r['adGroupCriterion'] for r in crit
                        if r['adGroupCriterion'].get('type') in ('AGE_RANGE', 'GENDER', 'PARENTAL_STATUS', 'INCOME_RANGE')]
    # 디맨드젠은 연령·성별을 '잠재고객(Audience)' 묶음으로 붙임 → 같은 Audience 를 그대로 연결
    au = tryq(ads, log, 'audience', "SELECT ad_group_criterion.audience.audience FROM ad_group_criterion "
              f"WHERE ad_group.resource_name = '{agrn}' AND ad_group_criterion.type = 'AUDIENCE' AND ad_group_criterion.status != 'REMOVED'") or []
    t['audiences'] = [r['adGroupCriterion']['audience']['audience'] for r in au if r['adGroupCriterion'].get('audience')]
    if t['audiences']: t['ag_criteria'] = []
    mf = ['marketing_images', 'square_marketing_images', 'portrait_marketing_images', 'tall_portrait_marketing_images',
          'logo_images', 'headlines', 'descriptions', 'business_name', 'call_to_action_text']
    ads_rows = tryq(ads, log, 'ad', "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.name, ad_group_ad.ad.final_urls FROM ad_group_ad "
                    f"WHERE ad_group.resource_name = '{agrn}' AND ad_group_ad.status != 'REMOVED'") or []
    t['ads'] = []
    for r in ads_rows:
        ad = r['adGroupAd']['ad']
        if ad.get('type') == 'DEMAND_GEN_MULTI_ASSET_AD':
            body = {}
            for f in mf:
                rr = tryq(ads, log, 'ad.' + f, f"SELECT ad_group_ad.ad.id, ad_group_ad.ad.demand_gen_multi_asset_ad.{f} FROM ad_group_ad "
                          f"WHERE ad_group.resource_name = '{agrn}' AND ad_group_ad.ad.id = {ad['id']}")
                if rr: deep_merge(body, rr[0]['adGroupAd']['ad'].get('demandGenMultiAssetAd', {}))
            t['ads'].append({'name': ad.get('name'), 'finalUrls': ad.get('finalUrls', []), 'demandGenMultiAssetAd': body})
        else:
            log.setdefault('notes', []).append(f"복제하지 않은 광고 형식: {ad.get('type')}")
    if not t['ads']:
        raise SystemExit('템플릿에서 복제할 디맨드젠 이미지 광고를 찾지 못했습니다.')
    return t


def clean(o):
    """API 가 돌려준 UNKNOWN/UNSPECIFIED 값은 생성 요청에 쓸 수 없어 제거"""
    if isinstance(o, dict):
        r = {k: clean(v) for k, v in o.items() if v not in ('UNKNOWN', 'UNSPECIFIED')}
        return {k: v for k, v in r.items() if v not in ({}, [], None)}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def deep_merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict): deep_merge(a[k], v)
        else: a[k] = v


def bidding(c):
    bt = c.get('biddingStrategyType')
    if bt == 'TARGET_SPEND':
        ts = {k: v for k, v in c.get('targetSpend', {}).items() if k == 'cpcBidCeilingMicros'}
        return {'targetSpend': ts}
    if bt == 'MAXIMIZE_CONVERSIONS':
        return {'maximizeConversions': {k: v for k, v in c.get('maximizeConversions', {}).items() if k == 'targetCpaMicros'}}
    if bt == 'TARGET_CPA':
        return {'targetCpa': c.get('targetCpa', {})}
    if bt == 'MAXIMIZE_CONVERSION_VALUE':
        return {'maximizeConversionValue': c.get('maximizeConversionValue', {})}
    if bt == 'MAXIMIZE_CLICKS':
        return {'maximizeClicks': {}}
    raise SystemExit(f'복제할 수 없는 입찰 방식: {bt}')


def build_campaign_ops(ads, t, cname, agname, budget_micros):
    cid = ads.cid
    c = t['campaign']
    b, cp, ag = f'customers/{cid}/campaignBudgets/-1', f'customers/{cid}/campaigns/-2', f'customers/{cid}/adGroups/-3'
    camp = {'resourceName': cp, 'name': cname, 'status': 'PAUSED', 'advertisingChannelType': c['advertisingChannelType'],
            'campaignBudget': b, 'containsEuPoliticalAdvertising': 'DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING'}
    camp.update(bidding(c))
    g = clean(c.get('geoTargetTypeSetting') or {})
    if g: camp['geoTargetTypeSetting'] = g
    if 'demandGenCampaignSettings' in c: camp['demandGenCampaignSettings'] = c['demandGenCampaignSettings']
    ops = [{'campaignBudgetOperation': {'create': {'resourceName': b, 'name': cname + ' 예산', 'amountMicros': str(budget_micros),
                                                    'deliveryMethod': t['budget'].get('deliveryMethod', 'STANDARD'),
                                                    'explicitlyShared': False}}},
           {'campaignOperation': {'create': camp}}]
    for lc in t['languages']:
        ops.append({'campaignCriterionOperation': {'create': {'campaign': cp, 'language': {'languageConstant': lc}}}})
    agb = {'resourceName': ag, 'campaign': cp, 'name': agname, 'status': 'ENABLED'}
    if t['ad_group'].get('type') not in (None, 'UNSPECIFIED', 'UNKNOWN'): agb['type'] = t['ad_group']['type']
    ops.append({'adGroupOperation': {'create': agb}})
    for a in t.get('audiences', []):
        ops.append({'adGroupCriterionOperation': {'create': {'adGroup': ag, 'audience': {'audience': a}}}})
    for cr in t['ag_criteria']:
        body = {'adGroup': ag, 'negative': bool(cr.get('negative'))}
        for k in ('ageRange', 'gender', 'parentalStatus', 'incomeRange'):
            if cr.get(k): body[k] = clean(cr[k])
        ops.append({'adGroupCriterionOperation': {'create': body}})
    for a in t['ads']:
        ad = {'finalUrls': a['finalUrls'], 'demandGenMultiAssetAd': clean(a['demandGenMultiAssetAd'])}
        if a.get('name'): ad['name'] = a['name']
        ops.append({'adGroupAdOperation': {'create': {'adGroup': ag, 'status': 'ENABLED', 'ad': ad}}})
    return ops


def brief(msg):
    """API 오류를 '메시지 @ 위치' 목록으로 줄임"""
    try:
        j = json.loads(msg[msg.index('{'):])
        out = []
        for d in j['error'].get('details', []):
            for e in d.get('errors', []):
                path = '.'.join(f"{p['fieldName']}" + (f"[{p['index']}]" if 'index' in p else '')
                                for p in e.get('location', {}).get('fieldPathElements', []))
                out.append(f"{e.get('message')} @ {path}")
        return out or [msg[:1500]]
    except Exception:
        return [msg[:1500]]


def gmutate(ads, ops, validate):
    body = {'mutateOperations': ops, 'partialFailure': False, 'validateOnly': validate}
    return _post(ads.base + '/googleAds:mutate', json.dumps(body).encode(), ads.h)


# ---------------------------------------------------------------- 실행
def main():
    validate = 'validate' in sys.argv
    live = env('GADS_LIVE') == '1'
    serve = env('GADS_SERVE') == '1'
    cap = min(500, int(env('GADS_MAX_RADIUS', '250')))
    plan = json.load(open('data/ads_plan.json'))
    pois = json.load(open('data/ads_pois.json'))
    tname = {t['key']: t['name'] for t in plan['types']}
    on = {t['key'] for t in plan['types'] if t['on']}
    share = {t['key']: t.get('share', 0) for t in plan['types']}
    log = {'time': plan['time'], 'mode': 'validate' if validate else ('live' if live else 'dry_run'), 'serve': serve,
           'types_on': [tname[k] for k in on], 'index_now': plan.get('index_now'), 'reach_now': plan.get('reach_now'),
           'cap_per_campaign': cap}

    # 거점 → 유형별 (좌표 중복 제거)
    want, scmap = {}, {}
    for p in pois:
        k = (round(p['lat'] * 1e6), round(p['lon'] * 1e6))
        if k in want.setdefault(p['t'], {}): continue
        want[p['t']][k] = p
        scmap[(p['t'], k)] = p.get('sc', 0)
    log['pois'] = {tname.get(t, t): len(v) for t, v in want.items()}
    log['pois_total'] = sum(len(v) for v in want.values())
    log['campaigns_needed'] = {tname.get(t, t): math.ceil(len(v) / cap) for t, v in want.items()}

    if missing():
        log['note'] = '비밀값 없음 → 계획만 기록'
        json.dump(log, open(LOG_FILE, 'w'), ensure_ascii=False, indent=1); print(log); return
    ads, ops = Ads(), Ops()
    log['ops_before'] = ops.used

    try:
        t = read_template(ads, log)
        if validate:
            o = build_campaign_ops(ads, t, f'{PREFIX} 검증용', '[하나맵|검증|1]', 10_000 * 1_000_000)
            log['template'] = {'campaign': t['campaign'].get('name'), 'bidding': t['campaign'].get('biddingStrategyType'),
                               'languages': len(t['languages']), 'ag_criteria': len(t['ag_criteria']), 'audiences': t.get('audiences'), 'ads': len(t['ads']),
                               'upgraded_targeting': t['campaign'].get('demandGenCampaignSettings')}
            log['validate_ops'] = len(o)
            try:
                gmutate(ads, o, True); log['validate'] = 'OK — 실제 생성 가능'
            except RuntimeError as e:
                log['validate'] = 'FAIL'; log['errors'] = brief(str(e))
            return

        # 현재 fleet
        rows = ads.search("SELECT campaign.resource_name, campaign.name, campaign.status, campaign.campaign_budget, "
                          "campaign_budget.amount_micros, campaign_budget.explicitly_shared, ad_group.resource_name, ad_group.name, ad_group.status "
                          f"FROM ad_group WHERE campaign.name LIKE '{PREFIX}%' AND campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'")
        fleet = {}   # campaign rn -> info
        for r in rows:
            m = AGTAG.match(r['adGroup']['name'] or '')
            if not m: continue
            cp = r['campaign']['resourceName']
            f = fleet.setdefault(cp, {'name': r['campaign']['name'], 'type': m.group(1), 'n': int(m.group(2)),
                                      'status': r['campaign']['status'], 'budget': r['campaign'].get('campaignBudget'),
                                      'amount': int(r.get('campaignBudget', {}).get('amountMicros', 0)),
                                      'shared': r.get('campaignBudget', {}).get('explicitlyShared'), 'ag': [], 'radii': {}})
            f['ag'].append((r['adGroup']['resourceName'], r['adGroup']['status']))
        if fleet:
            crit = ads.search("SELECT campaign.resource_name, campaign_criterion.resource_name, "
                              "campaign_criterion.proximity.geo_point.latitude_in_micro_degrees, "
                              "campaign_criterion.proximity.geo_point.longitude_in_micro_degrees FROM campaign_criterion "
                              f"WHERE campaign.name LIKE '{PREFIX}%' AND campaign_criterion.type = 'PROXIMITY'")
            for c in crit:
                cp = c['campaign']['resourceName']
                if cp not in fleet: continue
                gp = c['campaignCriterion'].get('proximity', {}).get('geoPoint', {})
                k = (int(gp.get('latitudeInMicroDegrees', 0)), int(gp.get('longitudeInMicroDegrees', 0)))
                fleet[cp]['radii'][k] = c['campaignCriterion']['resourceName']

        # ① 매시간 켜기/끄기 + 예산
        ctl = []
        for cp, f in fleet.items():
            is_on = f['type'] in on and len(f['radii']) > 0
            if serve:
                st = 'ENABLED' if is_on else 'PAUSED'
                if f['status'] != st:
                    ctl.append(('campaigns', {'update': {'resourceName': cp, 'status': st}, 'updateMask': 'status'}))
            else:
                if f['status'] != 'PAUSED':   # 테스트: 캠페인은 항상 일시중지
                    ctl.append(('campaigns', {'update': {'resourceName': cp, 'status': 'PAUSED'}, 'updateMask': 'status'}))
            for agrn, ast in f['ag']:
                st = 'ENABLED' if (is_on or serve) else 'PAUSED'
                if ast != st:
                    ctl.append(('adGroups', {'update': {'resourceName': agrn, 'status': st}, 'updateMask': 'status'}))
        base = env('GADS_DAILY_BUDGET_WON').replace(',', '')
        bud = []
        if base.isdigit() and fleet:
            lo, hi = float(env('GADS_INDEX_MIN', '0.2')), float(env('GADS_INDEX_MAX', '2.0'))
            fac = max(lo, min(hi, plan.get('index_now') or 1.0))
            day = int(base) * fac
            ssum = sum(share[k] for k in on) or 1
            log['budget'] = {'base_won': int(base), 'factor': round(fac, 3), 'today_rate_won': int(day), 'by_type': {}}
            for k in on:
                cps = [cp for cp, f in fleet.items() if f['type'] == k and f['radii'] and not f['shared']]
                w = {cp: sum(scmap.get((k, g), 0) for g in fleet[cp]['radii']) for cp in cps}
                ws = sum(w.values()) or 1
                tday = day * share[k] / ssum
                log['budget']['by_type'][tname[k]] = int(tday)
                for cp in cps:
                    won = max(1000, int(round(tday * w[cp] / ws / 10) * 10))
                    old = fleet[cp]['amount'] / 1e6
                    if old and abs(won - old) / old <= 0.05: continue
                    bud.append(('campaignBudgets', {'update': {'resourceName': fleet[cp]['budget'], 'amountMicros': str(won * 1_000_000)},
                                                    'updateMask': 'amount_micros'}))
        ctl += bud
        log['control_ops'] = len(ctl)
        if live and ctl:
            if len(ctl) > ops.left():
                log['note'] = '오늘 작업 한도 초과로 매시간 반영을 건너뜀'
            else:
                bysvc = {}
                for svc, op in ctl: bysvc.setdefault(svc, []).append(op)
                for svc in ('campaignBudgets', 'adGroups', 'campaigns'):
                    if bysvc.get(svc): ads.mutate(svc, bysvc[svc])
                ops.spend(len(ctl))

        # ② 반경 계획: 유형별로 빠질 것 삭제, 새 것은 빈자리 → 부족하면 새 캠페인
        plan_rm, plan_add, need_new = [], [], []
        for k, wk in want.items():
            cps = sorted([cp for cp, f in fleet.items() if f['type'] == k], key=lambda cp: fleet[cp]['n'])
            have = {}
            for cp in cps:
                for g, rn in fleet[cp]['radii'].items(): have[g] = (cp, rn)
            for g, (cp, rn) in have.items():
                if g not in wk: plan_rm.append(rn); fleet[cp]['radii'].pop(g)
            todo = [g for g in sorted(wk, key=lambda g: -wk[g].get('sc', 0)) if g not in have]
            for cp in cps:
                free = cap - len(fleet[cp]['radii'])
                while free > 0 and todo:
                    g = todo.pop(0); plan_add.append((cp, g, wk[g])); free -= 1
            n = max([fleet[cp]['n'] for cp in cps] or [0])
            while todo:
                n += 1
                chunk, todo = todo[:cap], todo[cap:]
                need_new.append((k, n, [(g, wk[g]) for g in chunk]))
        log['radius_plan'] = {'remove': len(plan_rm), 'add_existing': len(plan_add),
                              'new_campaigns': len(need_new), 'add_new': sum(len(c) for _, _, c in need_new)}

        if not live:
            log['note'] = 'GADS_LIVE 가 1 이 아니라 계획만 기록'; return
        done = {'removed': 0, 'added': 0, 'created': []}
        if plan_rm and ops.left() >= len(plan_rm):
            ads.mutate('campaignCriteria', [{'remove': rn} for rn in plan_rm]); ops.spend(len(plan_rm)); done['removed'] = len(plan_rm)

        def add_radii(items):
            items = items[:ops.left()]
            for i in range(0, len(items), 1000):
                part = items[i:i + 1000]
                ads.mutate('campaignCriteria', [{'create': {'campaign': cp, 'proximity': {
                    'geoPoint': {'latitudeInMicroDegrees': g[0], 'longitudeInMicroDegrees': g[1]},
                    'radius': 1, 'radiusUnits': 'KILOMETERS', 'address': addr_info(p.get('ad'), p['nm'])}}} for cp, g, p in part])
                ops.spend(len(part)); done['added'] += len(part)
            return len(items)

        add_radii(plan_add)
        start_won = max(1000, int(int(base or 10000) / max(1, sum(math.ceil(len(v) / cap) for v in want.values()))))
        for k, n, chunk in need_new:
            o = build_campaign_ops(ads, t, f'{PREFIX} {tname.get(k, k)} #{n}', f'[하나맵|{k}|{n}]', start_won * 1_000_000)
            if ops.left() < len(o) + len(chunk): break
            res = gmutate(ads, o, False); ops.spend(len(o))
            cp = next(r['campaignResult']['resourceName'] for r in res.get('mutateOperationResponses', []) if 'campaignResult' in r)
            # 새 캠페인은 광고그룹 '사용' 상태로 생성됨 — 캠페인이 일시중지라 노출 없음. 다음 시간 ①에서 유형 상태에 맞춰짐
            add_radii([(cp, g, p) for g, p in chunk])
            done['created'].append(f'{PREFIX} {tname.get(k, k)} #{n}')
        log['done'] = done
        rest = log['radius_plan']['add_existing'] + log['radius_plan']['add_new'] - done['added']
        log['radius_left'] = rest
        if rest: log['note'] = f'오늘 작업 한도로 반경 {rest}개는 다음 시간·다음 날 이어서 넣습니다.'
    except Exception as e:
        log['error'] = brief(str(e))
        raise
    finally:
        log['ops_used_today'] = ops.used
        json.dump(log, open(LOG_FILE, 'w'), ensure_ascii=False, indent=1)
        print(json.dumps(log, ensure_ascii=False)[:3000])


if __name__ == '__main__':
    main()
