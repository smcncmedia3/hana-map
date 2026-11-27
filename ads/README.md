# Google Ads 정시 자동 반영

매시 2분(KST) GitHub Actions `hana-ads.yml`이 돕니다.
1. `plan.py` — 사이트를 헤드리스 크롬으로 열어 화면과 **같은 계산**으로 지금 유형별 가중치를 뽑아 `data/ads_plan.json`에 저장 (사이트 파일은 바꾸지 않고 `hook.js`를 실행할 때만 끼워 넣음)
2. `sync.py` — 이름 끝에 `[유형]` 태그가 붙은 광고그룹을 **지금 +3% 이상 유형은 켜기, 나머지는 끄기**. 결과는 `data/ads_log.json`
3. (수동) `targets.py` — Actions → Run workflow → mode `targets`: 태그 광고그룹에 그 유형 거점의 **반경 1km 위치 타겟**을 맞춤. 캠페인당 500개 한도를 넘으면 건너뛰고 기록

## 광고그룹 이름 규칙
`하나_시니어_DG_병원_서울 [병원|서울]` — 끝의 대괄호가 태그. 태그 없는 광고그룹은 절대 건드리지 않습니다.
유형: 노인복지관 · 노인교실 · 전통시장 · 대형병원 · 병원 · 요양병원 · 보건소 · 등산로 · 공원 · 대형점포 · 라운지
지역(선택): 서울 부산 대구 인천 광주 대전 울산 세종 경기 강원 충북 충남 전북 전남 경북 경남 제주
병원(1,424)·요양병원(1,267)·보건소(1,551)·전통시장(1,253)·등산로(1,768)는 500개를 넘으므로 지역별로 나눠야 합니다.
Demand Gen 캠페인은 광고그룹 단위 위치 타겟(upgraded targeting)이 켜져 있어야 합니다.

## 저장소 설정 (Settings → Secrets and variables → Actions)
| 종류 | 이름 | 값 |
|---|---|---|
| Secret | `GADS_CLIENT_ID` | Google Cloud OAuth 클라이언트 ID |
| Secret | `GADS_CLIENT_SECRET` | OAuth 클라이언트 보안 비밀 |
| Secret | `GADS_REFRESH_TOKEN` | 범위 `https://www.googleapis.com/auth/adwords` 리프레시 토큰 |
| Secret | `GADS_DEVELOPER_TOKEN` | (선택) 2026.9 이후 필수 아님 |
| Variable | `GADS_CUSTOMER_ID` | 광고 계정 ID `123-456-7890` |
| Variable | `GADS_LOGIN_CUSTOMER_ID` | (MCC로 접근할 때) MCC ID |
| Variable | `GADS_LIVE` | `1`이면 실제 반영. 없으면 **모의 실행**(바꿀 내용만 기록) |
| Variable | `GADS_BUDGET_MODE` | (선택) `share` — 유형별 캠페인 예산을 지금 비중대로 배분(꺼진 유형도 최소 5%) |
| Variable | `GADS_DAILY_BUDGET_WON` | (선택) 하루 총예산, 원 단위 |
| Variable | `GADS_TARGETS_PRUNE` | (선택) `1`이면 목록에서 빠진 반경 타겟 삭제 |

## 인증 준비 (2026.9 이후 방식: 개발자 토큰 대신 Cloud 프로젝트 승인)
1. Google Cloud 콘솔 → 프로젝트 만들기 → Google Ads API 사용 설정 → Google Ads API 개요에서 Explorer(또는 Basic) 접근 신청
2. OAuth 동의 화면(외부·테스트 사용자에 광고 계정 이메일 추가) → 사용자 인증 정보 → OAuth 클라이언트 ID(웹 애플리케이션, 리디렉션 URI `https://developers.google.com/oauthplayground`)
3. OAuth Playground → 톱니바퀴 → Use your own OAuth credentials → 범위 `https://www.googleapis.com/auth/adwords` → 광고 계정 Google 로그인 → Exchange → Refresh token 복사
4. 위 표대로 Secrets/Variables 등록 → Actions에서 `hana-ads` 수동 실행 → `data/ads_log.json` 확인 → 이상 없으면 `GADS_LIVE=1`
