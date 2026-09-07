# 주택 분양·임대 공고 알리미

마이홈포털·LH·청약홈의 공고를 매일 자동으로 수집해 **대시보드 한 장**(`data/dashboard.html`)으로 보여줍니다.
접수중 / 접수예정 / 마감임박을 나눠 보고, 검색·필터가 되며, 매 실행마다 다시 그려집니다.

## 설치는 끝났습니다. 남은 건 두 가지

### 1. 공공데이터포털 인증키 발급 (약 10분, 본인 인증 필요)

1. [data.go.kr](https://www.data.go.kr) 회원가입 후 로그인
2. 아래 API에서 각각 **활용신청** 버튼을 누릅니다 (자동 승인, 무료)
   - [국토교통부_마이홈포털 공공주택 모집공고 조회 서비스](https://www.data.go.kr/data/15108420/openapi.do) — **행복주택이 여기로 들어옵니다.** LH·SH·GH 물량이 한데 모여 있어 가장 중요합니다
   - [한국토지주택공사_분양임대공고문 조회 서비스](https://www.data.go.kr/data/15058530/openapi.do)
   - [한국부동산원_청약홈 분양정보 조회 서비스](https://www.data.go.kr/data/15098547/openapi.do) — APT / 오피스텔 / 공공지원민간임대 오퍼레이션이 한 서비스에 묶여 있습니다
3. 마이페이지 → 오픈API → 인증키에서 **일반 인증키(Decoding)** 를 복사
4. `.env` 파일을 열어 `DATA_GO_KR_KEY=` 뒤에 붙여넣기

> Encoding 키가 아니라 **Decoding 키**입니다. 잘못 넣으면 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가 납니다.

### 2. (선택) 디스코드 웹훅

기본 출력은 대시보드입니다. 푸시 알림도 받고 싶을 때만 설정하세요.
채널 설정(톱니) → 연동 → 웹후크 → 새 웹후크 → **웹후크 URL 복사** →
`.env` 의 `DISCORD_WEBHOOK_URL=` 에 붙여넣고, 실행할 때 `--discord` 를 붙입니다.

## 실행

```powershell
# 로직 점검 (인증키 없이도 됨)
.\.venv\Scripts\python.exe scripts\selftest.py

# 응답이 제대로 오는지 확인
.\.venv\Scripts\python.exe scripts\probe.py lh_notice

# 지금 공고를 '이미 본 것'으로 저장 — 최초 1회만. 안 하면 수백 건이 한꺼번에 옵니다.
.\run.ps1 -Seed

# 알림기록을 남기지 않고 콘솔로만 확인 (대시보드는 갱신됨)
.\run.ps1 -DryRun

# 실제 실행 → data/dashboard.html 갱신
.\run.ps1
```

실행이 끝나면 `data/dashboard.html` 을 브라우저로 열면 됩니다.
북마크해두면 계속 같은 주소를 씁니다 (매 실행마다 같은 파일을 덮어씁니다).

## 자동 실행

### 권장: GitHub Actions + Pages (PC 가 꺼져 있어도 돈다)

`.github/workflows/daily.yml` 이 매일 09:10 / 18:10 (KST) 에 수집하고, 대시보드를
GitHub Pages 로 올린다. 폰에서는 그 주소를 북마크해두면 된다.

최초 1회 설정:

1. GitHub 에 **public** 저장소를 만들고 push
2. Settings → Secrets and variables → Actions → New repository secret
   → 이름 `DATA_GO_KR_KEY`, 값은 발급받은 Decoding 인증키
3. Settings → Pages → Source 를 **GitHub Actions** 로 변경
4. Actions 탭 → daily → Run workflow 로 한 번 돌려서 확인

`data/listings.db` 는 저장소에 커밋된다. 마이홈 API 가 마감된 공고를 응답에서
지우기 때문에, 이 DB 가 사실상 유일한 아카이브다.

### 대안: 이 PC 의 작업 스케줄러

```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1
```

매일 09:10, 18:10 에 실행한다. PC 가 꺼져 있으면 그날은 건너뛴다.

## 조건 바꾸기

`config/filters.yaml` — 지역, 제외 키워드, 강조 키워드, 마감 공고 처리
`config/sources.yaml` — 수집할 소스 켜고 끄기, 며칠 전 공고까지 볼지

수집 대상을 청년 지원금·정책으로 넓히려면 `config/sources.yaml` 에 온통청년
(youthcenter.go.kr) API를 추가하면 됩니다. 구조는 그대로 씁니다.
