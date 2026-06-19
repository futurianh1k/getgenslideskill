#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genspark AI Slides 템플릿 일괄 다운로더
=======================================

기능
----
https://www.genspark.ai/ai_slides 의 슬라이드 템플릿을
'카테고리(분류) -> 템플릿 카드 -> 다운로드(zip)' 순서로 자동 순회하며
각 zip 파일을 '카테고리 이름' 폴더에 저장한다.

동작 흐름 (첨부 화면 기준)
--------------------------
1. 카테고리 탭(예: 교육, 기업 전략, B2B 영업 ...) 클릭
2. 그리드에 노출된 템플릿 카드를 순서대로 클릭 -> 상세 모달 오픈
3. 모달의 ' 패키징 중... ' 상태가 끝나고 '다운로드' 버튼이 활성화되면 클릭
4. 발생한 zip 다운로드를 해당 카테고리 폴더에 저장
5. 모달을 닫고 다음 카드로 이동

설계 원칙
---------
- Genspark 는 로그인 기반 SPA 이므로 persistent context 로 세션을 유지한다
  (최초 1회 수동 로그인 후 프로필 폴더에 세션이 저장되어 재로그인 불필요).
- DOM 구조가 비공개이므로, 셀렉터는 텍스트/role 기반 + 자동 탐지 + CLI 오버라이드로
  최대한 견고하게 구성했다. 구조 변경 시 --inspect 모드로 후보를 점검해 튜닝한다.
- manifest.json 으로 진행 상황을 기록하여 중단 후 재실행(이어받기)을 지원한다.

빠른 시작
---------
  pip install playwright
  playwright install chromium
  python genspark_slide_downloader.py            # 최초 실행: 브라우저에서 수동 로그인 후 Enter
  python genspark_slide_downloader.py --categories "교육,컨설팅" --limit 3   # 일부만 테스트
  python genspark_slide_downloader.py --inspect  # 셀렉터 점검(스크린샷 + 접근성 트리 덤프)

주의
----
본 스크립트는 사용자 본인 계정에서 UI가 제공하는 '다운로드' 기능을 자동 클릭할 뿐이다.
서버 부하를 주지 않도록 카드/카테고리 사이에 지연을 둔다. 서비스 약관을 준수하여 사용한다.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout


# ============================================================
# 설정 (구조 변경 시 이 영역 또는 CLI 인자로 조정)  ※ 확인 필요
# ============================================================
BASE_URL = "https://www.genspark.ai/ai_slides?tab=featured"

# 다운로드 대상 기본 카테고리 (탭에 표시되는 한글 라벨 그대로).
# '전체/내 스킬/추천/과제' 는 콘텐츠 분류가 아니므로 기본 제외했다.
DEFAULT_CATEGORIES = [
    "기업 전략", "교육", "B2B 영업", "학술", "마케팅",
    "데이터 & KPI", "투자 유치", "공공 정책", "제품 관리",
    "컨설팅", "커리어", "AI 리터러시", "라이프", "디자인 크래프트",
]

# 모달의 다운로드 버튼에 표시되는 텍스트 / 패키징 진행 텍스트
DOWNLOAD_TEXT = "다운로드"
PACKAGING_TEXT = "패키징"

# 템플릿 카드 자동 탐지 후보 (위에서부터 시도, 가장 카드처럼 보이는 셀렉터 채택)
CARD_CANDIDATE_SELECTORS = [
    "[class*=template-card]",
    "[class*=templateCard]",
    "[class*=slide-card]",
    "[class*=card]:has(img)",
    "[class*=Card]:has(img)",
    "a:has(img):has(h1,h2,h3,h4,p,span)",
    "li:has(img)",
    "div[role='button']:has(img)",
]

# 모달(다이얼로그) 후보
MODAL_CANDIDATE_SELECTORS = [
    "[role=dialog]",
    "[class*=modal]",
    "[class*=Modal]",
    "[class*=dialog]",
    "[class*=Dialog]",
]


# ============================================================
# 유틸리티
# ============================================================
def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_name(name, default="untitled"):
    """파일/폴더명에 쓸 수 없는 문자를 정리한다."""
    if not name:
        return default
    name = _INVALID.sub("_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:120] if name else default


def unique_path(path: Path) -> Path:
    """동일 파일명이 있으면 _1, _2 ... 를 붙여 충돌을 피한다."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 1
    while True:
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


# ============================================================
# manifest (진행 기록 / 이어받기)
# ============================================================
def manifest_path(out_root: Path) -> Path:
    return out_root / "manifest.json"


def load_manifest(out_root: Path) -> dict:
    p = manifest_path(out_root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log("manifest.json 파싱 실패 -> 새로 시작", "WARN")
    return {}


def save_manifest(out_root: Path, manifest: dict):
    manifest_path(out_root).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def record(manifest: dict, category: str, key: str, filename, status: str):
    manifest.setdefault(category, [])
    # 동일 key 의 기존 기록은 갱신
    for e in manifest[category]:
        if e.get("key") == key:
            e.update({"filename": filename, "status": status,
                      "time": datetime.now().isoformat(timespec="seconds")})
            return
    manifest[category].append({
        "key": key, "filename": filename, "status": status,
        "time": datetime.now().isoformat(timespec="seconds"),
    })


def done_keys(manifest: dict, category: str) -> set:
    return {e["key"] for e in manifest.get(category, []) if e.get("status") == "ok"}


# ============================================================
# 브라우저 / 로그인
# ============================================================
def launch_context(p, args):
    """persistent context 로 브라우저를 띄운다(세션 유지)."""
    profile_dir = Path(args.profile).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=args.headless,
        accept_downloads=True,
        viewport=None,                      # 실제 창 크기 사용
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
    )
    context.set_default_timeout(args.element_timeout * 1000)
    return context


def ensure_logged_in(page, args):
    """로그인 여부를 보장한다. headed 모드에서는 수동 로그인 후 Enter 로 진행."""
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)

    if args.assume_logged_in:
        return
    if args.headless:
        log("headless 모드에서는 최초 로그인이 어렵다. 먼저 --headless 없이 1회 로그인하라.", "WARN")
        return

    print("\n" + "=" * 64)
    print(" 브라우저 창에서 Genspark 에 로그인하라.")
    print(" 슬라이드 템플릿 목록이 보이는 상태가 되면 이 터미널에서 Enter 를 누른다.")
    print(" (한 번 로그인하면 프로필 폴더에 세션이 저장되어 다음부터는 생략된다.)")
    print("=" * 64)
    try:
        input(" 로그인 완료 후 Enter > ")
    except EOFError:
        log("표준입력이 없어 대기를 건너뛴다. (--assume-logged-in 권장)", "WARN")


# ============================================================
# 카테고리
# ============================================================
def detect_category_labels(page):
    """탭 라벨을 자동 수집한다(role=tab 우선)."""
    labels = []
    try:
        tabs = page.get_by_role("tab")
        for i in range(tabs.count()):
            t = (tabs.nth(i).inner_text() or "").strip()
            if t and t not in labels:
                labels.append(t)
    except Exception:
        pass
    return labels


def resolve_categories(page, args):
    if args.categories:
        if args.categories.strip().lower() == "all":
            detected = detect_category_labels(page)
            if detected:
                log(f"자동 감지된 탭: {detected}")
                return detected
            log("탭 자동 감지 실패 -> 기본 카테고리 사용", "WARN")
            return DEFAULT_CATEGORIES
        return [c.strip() for c in args.categories.split(",") if c.strip()]
    return DEFAULT_CATEGORIES


def click_category(page, category):
    """카테고리 탭을 클릭한다. 여러 로케이터 전략을 순차 시도."""
    candidates = [
        lambda: page.get_by_role("tab", name=category, exact=True),
        lambda: page.get_by_role("button", name=category, exact=True),
        lambda: page.get_by_role("link", name=category, exact=True),
        lambda: page.get_by_text(category, exact=True),
    ]
    for make in candidates:
        try:
            loc = make()
            if loc.count() > 0:
                loc.first.scroll_into_view_if_needed(timeout=3000)
                loc.first.click(timeout=4000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    return False


# ============================================================
# 카드 탐지 / 그리드 로딩
# ============================================================
def detect_card_selector(page):
    """카드 후보 셀렉터 중 가장 적합한 것을 선택한다."""
    best, best_count = None, 0
    for sel in CARD_CANDIDATE_SELECTORS:
        try:
            loc = page.locator(sel)
            cnt = loc.count()
            if cnt < 3:
                continue
            # 첫 요소가 카드 크기(대략 폭>180, 높이>120)인지 확인
            box = loc.first.bounding_box()
            if not box or box["width"] < 180 or box["height"] < 120:
                continue
            if cnt > best_count:
                best, best_count = sel, cnt
        except Exception:
            continue
    return best


def load_all_cards(page, card_selector, args):
    """무한 스크롤로 모든 카드를 로드한다(카드 수가 더 늘지 않을 때까지)."""
    prev = -1
    stable = 0
    for _ in range(args.max_scrolls):
        try:
            cnt = page.locator(card_selector).count()
        except Exception:
            cnt = 0
        if cnt == prev:
            stable += 1
            if stable >= 2:        # 2회 연속 변화 없으면 종료
                break
        else:
            stable = 0
        prev = cnt
        # 페이지 끝까지 스크롤
        try:
            page.mouse.wheel(0, 4000)
        except Exception:
            pass
        page.wait_for_timeout(args.scroll_pause_ms)
    return prev if prev >= 0 else 0


# ============================================================
# 모달 / 다운로드
# ============================================================
def find_modal(page):
    for sel in MODAL_CANDIDATE_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def wait_modal_open(page, timeout_ms=8000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if find_modal(page) is not None:
            return True
        page.wait_for_timeout(300)
    # 모달 셀렉터를 못 찾아도, 다운로드 버튼이 보이면 열린 것으로 간주
    return _find_download_button(page) is not None


def get_modal_title(page):
    """모달의 대표 제목을 추출한다(파일명/헤딩 우선). 실패 시 None."""
    modal = find_modal(page)
    scopes = [modal] if modal is not None else [page]
    for scope in scopes:
        # 1) 헤딩류
        for css in ["h1", "h2", "h3", "h4"]:
            try:
                loc = scope.locator(css)
                if loc.count() > 0:
                    t = (loc.first.inner_text() or "").strip()
                    if t:
                        return t.splitlines()[0].strip()
            except Exception:
                continue
    return None


def _find_download_button(page):
    """모달 내 '다운로드' 버튼 로케이터를 반환(없으면 None)."""
    modal = find_modal(page)
    scope = modal if modal is not None else page
    candidates = [
        lambda: scope.get_by_role("button", name=DOWNLOAD_TEXT, exact=False),
        lambda: scope.locator(f"button:has-text('{DOWNLOAD_TEXT}')"),
        lambda: scope.get_by_text(DOWNLOAD_TEXT, exact=True),
    ]
    for make in candidates:
        try:
            loc = make()
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def wait_download_ready(page, args):
    """'패키징 중...'이 끝나고 '다운로드' 버튼이 활성화될 때까지 대기 후 반환."""
    deadline = time.time() + args.package_timeout
    while time.time() < deadline:
        btn = _find_download_button(page)
        if btn is not None:
            try:
                if btn.is_enabled():
                    return btn
            except Exception:
                return btn       # is_enabled 판단 불가 시 일단 반환
        page.wait_for_timeout(700)
    btn = _find_download_button(page)
    if btn is not None:
        return btn
    raise RuntimeError("다운로드 버튼을 찾지 못함(패키징 시간 초과 또는 셀렉터 불일치)")


def close_modal(page):
    """모달을 닫는다(닫기 버튼 -> 실패 시 ESC)."""
    modal = find_modal(page)
    scope = modal if modal is not None else page
    close_candidates = [
        lambda: scope.get_by_role("button", name=re.compile("close|닫기|×|✕", re.I)),
        lambda: scope.locator("button[aria-label*='close' i]"),
        lambda: scope.locator("[class*=close]"),
    ]
    for make in close_candidates:
        try:
            loc = make()
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2000)
                page.wait_for_timeout(500)
                if find_modal(page) is None:
                    return
        except Exception:
            continue
    # 최후수단: ESC
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass


def download_one(page, card_locator, index, cat_dir: Path, args):
    """카드 1개를 열어 zip 을 받고 카테고리 폴더에 저장한다. (저장명, dedup_key) 반환."""
    card_locator.scroll_into_view_if_needed(timeout=4000)
    card_locator.click(timeout=5000)

    if not wait_modal_open(page):
        close_modal(page)
        raise RuntimeError("모달이 열리지 않음")

    title = get_modal_title(page) or f"item_{index+1}"
    dedup_key = title

    btn = wait_download_ready(page, args)
    with page.expect_download(timeout=args.dl_timeout * 1000) as di:
        btn.click()
    dl = di.value

    suggested = dl.suggested_filename or f"{sanitize_name(title)}.zip"
    if not suggested.lower().endswith(".zip"):
        suggested = suggested + ".zip"
    target = unique_path(cat_dir / sanitize_name(suggested))
    dl.save_as(str(target))

    close_modal(page)
    return target.name, dedup_key


# ============================================================
# 카테고리 처리
# ============================================================
def process_category(page, category, out_root: Path, manifest: dict, args):
    log(f"==== 카테고리: {category} ====")
    if not click_category(page, category):
        log(f"탭을 찾지 못함: '{category}' (라벨/셀렉터 확인 필요)", "WARN")
        return
    page.wait_for_timeout(1200)

    # 카드 셀렉터 결정 (CLI 우선 -> 자동 탐지)
    card_selector = args.card_selector or detect_card_selector(page)
    if not card_selector:
        log("템플릿 카드를 탐지하지 못함. --inspect 로 구조 확인 후 --card-selector 지정 필요", "WARN")
        return
    log(f"카드 셀렉터: {card_selector}")

    total = load_all_cards(page, card_selector, args)
    log(f"템플릿 {total}개 로드됨")

    cat_dir = out_root / sanitize_name(category)
    cat_dir.mkdir(parents=True, exist_ok=True)

    already = done_keys(manifest, category) if not args.no_resume else set()
    processed = 0

    for i in range(total):
        if args.limit and processed >= args.limit:
            log(f"--limit {args.limit} 도달, 다음 카테고리로 이동")
            break

        # 매 반복마다 로케이터 재바인딩(모달 닫힘 후 DOM 재렌더 대비)
        try:
            card = page.locator(card_selector).nth(i)
        except Exception:
            log(f"[{i+1}/{total}] 카드 로케이터 실패 -> 건너뜀", "WARN")
            continue

        # 이어받기: 제목으로 빠르게 판단하려면 모달을 열어야 하므로,
        # manifest 의 key(제목) 사전 매칭은 다운로드 후 검증으로 처리한다.
        try:
            fname, key = download_one(page, card, i, cat_dir, args)
            if key in already:
                # 이미 받은 항목을 다시 받은 경우(드묾): 방금 받은 중복 파일 제거
                dup = cat_dir / fname
                try:
                    dup.unlink(missing_ok=True)
                except Exception:
                    pass
                log(f"[{i+1}/{total}] 이미 완료된 항목: {key} (중복 다운로드 정리)")
            else:
                record(manifest, category, key, fname, "ok")
                already.add(key)
                processed += 1
                log(f"[{i+1}/{total}] 저장 완료: {fname}  <-  {key}")
        except Exception as e:
            log(f"[{i+1}/{total}] 실패: {e}", "ERROR")
            record(manifest, category, f"index_{i}", None, f"error: {e}")
            close_modal(page)

        save_manifest(out_root, manifest)
        page.wait_for_timeout(int(args.delay * 1000))

    log(f"카테고리 '{category}' 완료: 이번 실행에서 {processed}개 신규 저장")


# ============================================================
# inspect 모드 (셀렉터 점검용)
# ============================================================
def run_inspect(page, out_root: Path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    insp = out_root / f"inspect_{stamp}"
    insp.mkdir(parents=True, exist_ok=True)

    # 스크린샷
    try:
        page.screenshot(path=str(insp / "page.png"), full_page=True)
        log(f"스크린샷 저장: {insp/'page.png'}")
    except Exception as e:
        log(f"스크린샷 실패: {e}", "WARN")

    # 접근성 트리
    try:
        snap = page.accessibility.snapshot()
        (insp / "a11y.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"접근성 트리 저장: {insp/'a11y.json'}")
    except Exception as e:
        log(f"접근성 트리 실패: {e}", "WARN")

    # 탭 라벨
    labels = detect_category_labels(page)
    log(f"감지된 탭 라벨({len(labels)}): {labels}")

    # 카드 후보 셀렉터별 개수
    report = ["# 카드 후보 셀렉터 개수\n"]
    for sel in CARD_CANDIDATE_SELECTORS:
        try:
            cnt = page.locator(sel).count()
        except Exception:
            cnt = -1
        report.append(f"{cnt:>4}  {sel}")
    auto = detect_card_selector(page)
    report.append(f"\n자동 선택된 카드 셀렉터: {auto}")
    (insp / "card_candidates.txt").write_text("\n".join(report), encoding="utf-8")
    log("카드 후보 리포트 저장: " + str(insp / "card_candidates.txt"))
    print("\n".join(report))


# ============================================================
# CLI
# ============================================================
def parse_args():
    ap = argparse.ArgumentParser(
        description="Genspark AI Slides 템플릿을 카테고리별로 일괄 다운로드한다.")
    ap.add_argument("--output", default="downloads",
                    help="저장 루트 폴더 (기본: downloads). 하위에 카테고리 폴더 생성")
    ap.add_argument("--profile", default=".gsprofile",
                    help="브라우저 프로필 폴더 (로그인 세션 유지)")
    ap.add_argument("--categories", default="",
                    help="대상 카테고리 콤마 구분 (예: '교육,컨설팅'). 'all'=탭 자동감지. 미지정=기본목록")
    ap.add_argument("--limit", type=int, default=0,
                    help="카테고리당 최대 다운로드 수 (0=무제한, 테스트 시 3 권장)")
    ap.add_argument("--card-selector", default="",
                    help="카드 CSS 셀렉터 강제 지정(자동 탐지 실패 시)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="카드 간 지연(초). 서버 보호용")
    ap.add_argument("--scroll-pause-ms", type=int, default=900,
                    help="스크롤 후 대기(ms)")
    ap.add_argument("--max-scrolls", type=int, default=60,
                    help="최대 스크롤 횟수")
    ap.add_argument("--package-timeout", type=int, default=120,
                    help="'패키징 중...' 대기 최대 시간(초)")
    ap.add_argument("--dl-timeout", type=int, default=180,
                    help="다운로드 이벤트 대기 최대 시간(초)")
    ap.add_argument("--element-timeout", type=int, default=15,
                    help="요소 기본 대기(초)")
    ap.add_argument("--headless", action="store_true",
                    help="헤드리스 실행(최초 로그인 후에만 권장)")
    ap.add_argument("--assume-logged-in", action="store_true",
                    help="로그인 대기 프롬프트 생략(이미 세션 있음)")
    ap.add_argument("--no-resume", action="store_true",
                    help="manifest 무시하고 처음부터")
    ap.add_argument("--inspect", action="store_true",
                    help="셀렉터 점검(스크린샷+접근성 트리+카드 후보 리포트) 후 종료")
    return ap.parse_args()


def main():
    args = parse_args()
    out_root = Path(args.output).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_root)

    log(f"저장 루트: {out_root}")
    log(f"프로필: {Path(args.profile).resolve()}")

    with sync_playwright() as p:
        context = launch_context(p, args)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_logged_in(page, args)

            if args.inspect:
                run_inspect(page, out_root)
                return

            categories = resolve_categories(page, args)
            log(f"대상 카테고리({len(categories)}): {categories}")

            for cat in categories:
                try:
                    process_category(page, cat, out_root, manifest, args)
                except Exception as e:
                    log(f"카테고리 처리 중 예외: {cat} -> {e}", "ERROR")
                finally:
                    save_manifest(out_root, manifest)

            log("모든 작업 완료.")
        finally:
            save_manifest(out_root, manifest)
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("사용자 중단(Ctrl+C). manifest 로 다음에 이어받기 가능.", "WARN")
        sys.exit(130)
