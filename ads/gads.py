"""Google Ads API(REST) 최소 클라이언트. 비밀값은 모두 환경변수(GitHub Secrets)로만 받습니다."""
import os, json, urllib.request, urllib.parse

VER = os.environ.get('GADS_API_VERSION', 'v26')
ENV = {k: os.environ.get(k, '').strip() for k in
       ['GADS_CLIENT_ID', 'GADS_CLIENT_SECRET', 'GADS_REFRESH_TOKEN', 'GADS_CUSTOMER_ID', 'GADS_LOGIN_CUSTOMER_ID', 'GADS_DEVELOPER_TOKEN']}
REQUIRED = ['GADS_CLIENT_ID', 'GADS_CLIENT_SECRET', 'GADS_REFRESH_TOKEN', 'GADS_CUSTOMER_ID']

def missing():
    return [k for k in REQUIRED if not ENV[k]]

def _post(url, body, headers):
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{e.code} {e.read().decode()[:20000]}')

class Ads:
    def __init__(self):
        tok = _post('https://oauth2.googleapis.com/token', urllib.parse.urlencode({
            'client_id': ENV['GADS_CLIENT_ID'], 'client_secret': ENV['GADS_CLIENT_SECRET'],
            'refresh_token': ENV['GADS_REFRESH_TOKEN'], 'grant_type': 'refresh_token'}).encode(),
            {'Content-Type': 'application/x-www-form-urlencoded'})
        self.cid = ENV['GADS_CUSTOMER_ID'].replace('-', '')
        self.h = {'Authorization': 'Bearer ' + tok['access_token'], 'Content-Type': 'application/json'}
        if ENV['GADS_DEVELOPER_TOKEN']:  # 2026.9 이후 선택 사항
            self.h['developer-token'] = ENV['GADS_DEVELOPER_TOKEN']
        if ENV['GADS_LOGIN_CUSTOMER_ID']:
            self.h['login-customer-id'] = ENV['GADS_LOGIN_CUSTOMER_ID'].replace('-', '')
        self.base = f'https://googleads.googleapis.com/{VER}/customers/{self.cid}'

    def search(self, q):
        rows, token = [], None
        while True:
            body = {'query': q}
            if token: body['pageToken'] = token
            r = _post(self.base + '/googleAds:search', json.dumps(body).encode(), self.h)
            rows += r.get('results', [])
            token = r.get('nextPageToken')
            if not token: return rows

    def mutate(self, service, operations, validate_only=False):
        out = []
        for i in range(0, len(operations), 1000):
            body = {'operations': operations[i:i + 1000], 'partialFailure': False, 'validateOnly': validate_only}
            out.append(_post(f'{self.base}/{service}:mutate', json.dumps(body).encode(), self.h))
        return out
