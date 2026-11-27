"""사이트와 같은 계산으로 지금 시각의 유형별 가중치를 뽑아 data/ads_plan.json 에 저장.
사이트(index.html)를 헤드리스 크롬으로 열면서 ads/hook.js 를 끼워 넣어 화면과 같은 계산 결과를 꺼냅니다(사이트 파일은 그대로)."""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

URL = os.environ.get('HANA_SITE_URL', 'https://smcncmedia3.github.io/hana-map/')
WITH_POIS = '--pois' in sys.argv

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--allow-file-access-from-files'])
        pg = await b.new_page(viewport={'width': 1600, 'height': 900})
        hook = open(os.path.join(os.path.dirname(__file__), 'hook.js')).read()
        anchor = 'let RS=null, RULE=null, ACT=new Set();'
        async def inject(route):
            r = await route.fetch()
            body = await r.text()
            if anchor not in body:
                raise SystemExit('index.html 구조가 바뀌어 훅을 넣을 수 없습니다(ads/hook.js ANCHOR 확인)')
            await route.fulfill(response=r, body=body.replace(anchor, anchor + hook, 1))
        await pg.route(lambda u: u.split('?')[0].rstrip('/') == URL.split('?')[0].rstrip('/') or u.endswith('index.html'), inject)
        await pg.goto(URL, wait_until='load', timeout=120000)
        await pg.wait_for_function('window.hanaAds && window.hanaAds()', timeout=120000)
        r = await pg.evaluate(f'window.hanaAds({str(WITH_POIS).lower()})')
        await b.close()
    os.makedirs('data', exist_ok=True)
    if WITH_POIS:
        json.dump(r.pop('pois'), open('data/ads_pois.json', 'w'), ensure_ascii=False, separators=(',', ':'))
    json.dump(r, open('data/ads_plan.json', 'w'), ensure_ascii=False, indent=1)
    on = [f"{t['name']} {t['now']-1:+.0%}" for t in r['types'] if t['on']]
    print(r['time'], r['dayType'], '타겟:', ', '.join(on) or '없음')

asyncio.run(main())
