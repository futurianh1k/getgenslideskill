# getgenslideskill

Genspark **AI Slides** 템플릿을 **카테고리(분류)별로 자동 다운로드**하여
각 분류 이름의 폴더에 zip 파일로 저장하는 자동화 스크립트이다.

대상 페이지: `https://www.genspark.ai/ai_slides?tab=featured`

---

## 1. 동작 개요

첨부된 UI 흐름을 그대로 자동화한다.

1. 현재 화면의 카테고리 탭(예: `교육`, `기업 전략`, `B2B 영업` ...)을 순서대로 클릭
2. 현재 화면의 템플릿 카드 한 행을 왼쪽에서 오른쪽 순서로 클릭하여 상세 모달 오픈
3. 모달의 `패키징 중...` 상태가 끝나고 `다운로드` 버튼이 활성화되면 클릭
4. 발생한 zip 다운로드를 `downloads/<카테고리>/` 폴더에 저장
5. 썸네일 URL, 제목, 파일명, 클릭 가능한 파일 경로를 `downloads/slides.csv`에 즉시 기록
6. 한 행을 마치면 카드 한 행 높이만큼 스크롤하고 다음 행을 같은 방식으로 처리
7. 현재 보이는 카테고리를 모두 마치면 오른쪽 화살표를 눌러 숨겨진 분류를 노출하고 반복

결과 구조 예시:

```
downloads/
├── 교육/
│   ├── ehs-safety-drill-deck.zip
│   ├── cfa-l1-quant-methods.zip
│   └── ...
├── 컨설팅/
│   └── ...
├── slides.csv        # 썸네일 및 다운로드 파일 목록(UTF-8 BOM)
└── manifest.json     # 진행 기록(이어받기용)
```

---

## 2. 사전 준비 / 설치

Python 3.9 이상에서 동작한다.

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 3. 사용법

### 3.1 최초 실행 (로그인)

Genspark 는 로그인이 필요하다. 최초 1회는 **브라우저 창을 띄운 상태**로 실행하여
직접 로그인한다. 로그인 세션은 `--profile` 폴더에 저장되어 다음부터는 자동 유지된다.

```bash
python genspark_slide_downloader.py
```

- 브라우저 창이 뜨면 Genspark 에 로그인한다.
- 슬라이드 템플릿 목록이 보이는 상태가 되면 **터미널에서 Enter** 를 누른다.
- 이후 기본 카테고리 목록을 순회하며 다운로드를 시작한다.

### 3.2 일부 카테고리만 / 테스트

먼저 소량으로 동작을 검증할 것을 권장한다.

```bash
# '교육','컨설팅' 카테고리만, 각 3개씩만 받아 동작 확인
python genspark_slide_downloader.py --categories "교육,컨설팅" --limit 3
```

### 3.3 전체 카테고리 (탭 자동 감지)

```bash
python genspark_slide_downloader.py --categories all
```

### 3.4 재실행(이어받기)

`slides.csv`의 썸네일 URL·제목·파일 경로와 `manifest.json`을 함께 확인하므로,
중단 후 같은 명령을 다시 실행하면 실제 파일이 남아 있는 항목은 카드 클릭 전 건너뛴다.
`--no-resume`은 manifest만 무시하며 CSV에 기록된 기존 다운로드는 계속 건너뛴다.

### 3.5 두 번째 실행부터 로그인 생략

세션이 저장된 뒤에는 프롬프트를 생략할 수 있다.

```bash
python genspark_slide_downloader.py --assume-logged-in
```

---

## 4. 주요 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `--output` | 저장 루트 폴더(하위에 카테고리 폴더 생성) | `downloads` |
| `--csv` | 결과 CSV 경로(상대 경로는 `--output` 기준) | `slides.csv` |
| `--profile` | 브라우저 프로필 폴더(로그인 세션 유지) | `.gsprofile` |
| `--categories` | 콤마 구분 카테고리. `all` 또는 미지정=화살표 포함 전체 자동 순회 | 전체 자동 순회 |
| `--limit` | 카테고리당 최대 다운로드 수(0=무제한) | `0` |
| `--card-selector` | 카드 CSS 셀렉터 강제 지정 | 자동 탐지 |
| `--delay` | 카드 간 지연(초) | `2.0` |
| `--scroll-pause-ms` | 한 행 스크롤 후 lazy-load 대기(ms) | `900` |
| `--row-gap` | 한 행 처리 후 추가 스크롤 간격(px) | `36` |
| `--max-scrolls` | 카테고리별 최대 행 스크롤 횟수 | `60` |
| `--category-next-selector` | 분류 캐러셀 오른쪽 화살표 셀렉터 | `.ds-cat-tabs-arrow--right` |
| `--max-category-pages` | 분류 캐러셀 최대 오른쪽 이동 페이지 수 | `20` |
| `--category-scroll-pause-ms` | 분류 화살표 클릭 후 대기(ms) | `700` |
| `--package-timeout` | `패키징 중...` 대기 최대(초) | `120` |
| `--dl-timeout` | 다운로드 이벤트 대기 최대(초) | `180` |
| `--headless` | 헤드리스 실행(최초 로그인 후에만) | off |
| `--assume-logged-in` | 로그인 대기 프롬프트 생략 | off |
| `--no-resume` | manifest 무시하고 처음부터 | off |
| `--inspect` | 셀렉터 점검 후 종료 | off |

카테고리 DOM 자동 감지 실패 시 사용하는 폴백 목록(`DEFAULT_CATEGORIES`)은
스크립트 상단에서 수정한다. 현재 값: `기업 전략, 교육, B2B 영업, 학술, 마케팅,
데이터 & KPI, 투자 유치, 공공 정책, 제품 관리, 컨설팅, 커리어, AI 리터러시,
라이프, 디자인 크래프트`

CSV 열은 `썸네일 이미지`, `썸네일 제목`, `파일 이름`, `파일 경로 링크` 순서다.
`썸네일 이미지`에는 원본 이미지 URL, `파일 경로 링크`에는 `file:///...` 형식의
절대 경로가 저장된다. 같은 파일 경로는 재실행 시 새 행을 만들지 않고 갱신한다.

---

## 5. 셀렉터 튜닝 (구조가 바뀌어 동작하지 않을 때)

Genspark 의 DOM 클래스명은 비공개이며 변경될 수 있다. 카드/버튼을 못 찾으면
`--inspect` 로 현재 페이지 구조를 점검한다.

```bash
python genspark_slide_downloader.py --inspect
# (로그인 후 Enter)
```

`inspect_<날짜시간>/` 폴더에 다음이 생성된다.

- `page.png` : 전체 스크린샷
- `a11y.json` : 접근성 트리(요소 이름/역할)
- `card_candidates.txt` : 카드 후보 셀렉터별 매칭 개수 + 자동 선택 결과

리포트를 보고 가장 카드 수에 부합하는 셀렉터를 골라 다음처럼 지정한다.

```bash
python genspark_slide_downloader.py --card-selector "div.your-card-class"
```

버튼/모달/카드 후보 목록은 스크립트 상단의
`DOWNLOAD_TEXT`, `PACKAGING_TEXT`, `CARD_CANDIDATE_SELECTORS`,
`MODAL_CANDIDATE_SELECTORS` 에서 조정한다.

---

## 6. 로그인 대안 — 기존 Chrome 에 연결 (선택)

Google OAuth/2FA 등으로 새 프로필 로그인이 번거로우면, 이미 로그인된
기존 Chrome 에 디버깅 포트로 붙는 방식도 가능하다. (본 스크립트는 persistent
context 가 기본이며, CDP 연결은 아래처럼 코드를 소폭 바꿔 사용한다.)

1. 기존 Chrome 을 디버깅 포트로 실행

   - Windows:
     ```
     "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\gschrome"
     ```
   - macOS:
     ```
     /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="$HOME/gschrome"
     ```

2. 그 창에서 Genspark 에 로그인한 뒤, `launch_context()` 를 아래로 교체

   ```python
   browser = p.chromium.connect_over_cdp("http://localhost:9222")
   context = browser.contexts[0]
   ```

---

## 7. 문제 해결

- **카드를 못 찾음 / 0개 로드**
  `--inspect` 의 `card_candidates.txt` 확인 후 `--card-selector` 로 지정.
- **`다운로드` 버튼을 못 찾음**
  버튼 라벨이 다를 수 있다. `DOWNLOAD_TEXT` 값을 실제 표기로 수정.
- **패키징이 오래 걸려 타임아웃**
  `--package-timeout`, `--dl-timeout` 값을 늘린다.
- **로그인 창이 떠도 목록이 안 보임**
  지역/플랜에 따라 메뉴 경로가 다를 수 있다. 로그인 후 직접
  `ai_slides` 화면으로 이동한 뒤 Enter.
- **중간에 끊김**
  같은 명령을 다시 실행하면 `manifest.json` 기준으로 이어받는다.

---

## 8. 주의사항

- 본 스크립트는 **사용자 본인 계정**에서 UI가 제공하는 `다운로드` 버튼을
  자동 클릭하는 도구이다.
- 서버 부하 방지를 위해 카드/카테고리 사이에 지연(`--delay`)을 둔다.
- Genspark 의 **서비스 약관 및 템플릿 라이선스**를 준수하여 사용한다.
- `.gsprofile/` 에는 로그인 세션이 저장되므로 저장소에 커밋하지 않는다
  (`.gitignore` 에 포함됨).

---

## 9. 로컬 템플릿 라이브러리

수집한 ZIP 템플릿을 Genspark 스타일의 카드 그리드와 팝업 뷰어로 탐색하는
React 웹 앱이 포함되어 있다. `slides.csv`가 있으면 제목과 썸네일 정보를 사용하고,
CSV가 아직 없더라도 `downloads/**/*.zip` 파일을 자동으로 찾는다.

```bash
npm install
npm run build
npm start
```

브라우저에서 `http://127.0.0.1:4173`을 연다. 개발 중에는 다음 명령을 사용한다.

```bash
npm run dev
```

- 분류 탭과 검색으로 템플릿을 필터링한다.
- 카드를 클릭하면 ZIP의 `previews/` 또는 `thumbnails/` 이미지를 팝업에서 탐색한다.
- 방향키로 시안을 넘기고 원본 ZIP을 다시 다운로드할 수 있다.
- ZIP은 디스크에 임시 해제하지 않고 필요한 이미지만 로컬 API가 읽어 전달한다.
- 다른 다운로드 폴더를 사용하려면 `SLIDES_DIR`, CSV 경로는 `SLIDES_CSV`,
  포트는 `PORT` 환경 변수로 지정한다.
