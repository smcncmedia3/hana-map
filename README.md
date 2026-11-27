# 하나은행 시니어 자산관리 타겟팅 맵

5064(50~64세) 유동인구를 시간대별로 예측하고, 시니어가 모이는 거점(병원·전통시장·공원·등산로 등)의 1km 반경에 시간대 가중치를 주는 YouTube(Google Ads) 반경 타겟팅용 지도입니다.

- 사이트: https://smcncmedia3.github.io/hana-map/
- 계산은 모두 브라우저에서 합니다(서버 없음). 데이터는 `js/*.js`에 들어 있습니다.

## 파일 구성

| 경로 | 내용 |
|---|---|
| `index.html` | 화면·모델 코드 (폰트·d3 포함) |
| `js/topo.js` | 전국 행정동·시군구 경계 (TopoJSON) |
| `js/pack.js` | 시군구 단위 예측 모델 (서울 2024 생활인구 학습, 전이 모델, 기상·달력 계수) |
| `js/pack2.js` | 행정동 3,559곳 (주민등록 5064, 주중·주말 시간 패턴, 거점 흡인 계수) |
| `js/pack3.js`, `js/pack3u.js` | 거점 8,495곳 좌표, 1km 반경별 동 면적 비율, 커버리지 격자 / 유형별 반경 합집합 |
| `js/rivers.js` | 하천 |
| `data/live.json` | 실시간 기상·특보·미세먼지 (Actions가 매시 47분 갱신) |
| `data/osm_pois.json` | OpenStreetMap 거점 수집본 |
| `feed/fetch_kma.py` | 기상청·에어코리아 수집 (시크릿 `DATA_GO_KR_KEY` 필요) |
| `feed/osm_pois.py` | OSM 거점 수집 (수동 실행) |
| `ads/` | Google Ads 정시 자동 반영 스크립트 (설정은 `ads/README.md`) |
| `data/ads_plan.json`, `data/ads_log.json` | 정시 유형별 가중치 계산 결과 / Google Ads 반영 기록 |
| `.github/workflows/` | `hana-weather.yml`(매시), `hana-ads.yml`(매시 Google Ads 반영), `hana-osm.yml`(수동) |

## 설정

1. Settings → Pages → Branch `main` / `(root)` 로 배포
2. Settings → Secrets and variables → Actions → `DATA_GO_KR_KEY` (공공데이터포털 일반 인증키, 기상청 단기예보·특보, 에어코리아 활용신청 필요)
3. 키가 없으면 화면은 Open-Meteo → 평년값으로 자동 대체합니다.

데이터를 다시 만들거나 모델을 고칠 때는 인계 패키지의 빌드 스크립트를 쓰세요.
