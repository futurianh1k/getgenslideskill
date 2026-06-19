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
2. 화면에 노출된 한 행의 템플릿 카드를 왼쪽부터 클릭 -> 상세 모달 오픈
3. 모달의 ' 패키징 중... ' 상태가 끝나고 '다운로드' 버튼이 활성화되면 클릭
4. 발생한 zip 다운로드를 해당 카테고리 폴더에 저장
5. 한 행 완료 후 카드 한 행 높이만큼 스크롤하고 다음 행으로 이동
6. 현재 보이는 카테고리를 마치면 오른쪽 화살표로 숨겨진 분류를 노출해 반복

재실행 시 slides.csv의 썸네일 URL/제목/파일 경로를 확인해 기존 ZIP은 카드 클릭 전 생략한다.

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
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import sync_playwright


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
    # 현재 Genspark DOM. 정확한 토큰 매칭을 써야 내부의
    # ds-card-thumb-slide / ds-card-thumb-img 를 카드로 오인하지 않는다.
    ".ds-card:not(.ds-card-new):has(img)",
    "[class~='ds-card']:not([class~='ds-card-new']):has(img)",
    "[class*=template-card]",
    "[class*=templateCard]",
    "[class*=slide-card]",
    "[class*=card]:has(img):not([class*=thumb])",
    "[class*=Card]:has(img):not([class*=Thumb])",
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

CSV_HEADERS = ("썸네일 이미지", "썸네일 제목", "파일 이름", "파일 경로 링크")
CATEGORY_EXCLUDED_LABELS = {"전체", "내 스킬", "추천", "과제"}
CATEGORY_ITEM_SELECTOR = ".ds-cat-tab"
CATEGORY_NAME_SELECTOR = ".ds-cat-tab-name"
CATEGORY_WRAP_SELECTOR = ".ds-cat-tabs-wrap"
CATEGORY_TABS_SELECTOR = ".ds-cat-tabs"
CATEGORY_NEXT_SELECTOR = ".ds-cat-tabs-arrow--right"


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


def resolve_csv_path(out_root: Path, csv_name: str) -> Path:
    """상대 CSV 경로는 다운로드 루트 기준으로 해석한다."""
    path = Path(csv_name)
    return path.resolve() if path.is_absolute() else (out_root / path).resolve()


def csv_safe(value: str) -> str:
    """Excel에서 외부 문자열이 수식으로 실행되는 CSV injection을 막는다."""
    value = str(value or "")
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv_entry(csv_path: Path, thumbnail_url: str, title: str, target: Path):
    """다운로드 결과 한 행을 추가하거나 같은 파일 경로의 기존 행을 갱신한다."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    row = {
        "썸네일 이미지": csv_safe(thumbnail_url),
        "썸네일 제목": csv_safe(title or target.stem),
        "파일 이름": csv_safe(target.name),
        "파일 경로 링크": target.as_uri(),
    }

    rows = []
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
                rows = [dict(item) for item in csv.DictReader(fp)]
        except Exception:
            log(f"기존 CSV를 읽지 못해 새로 작성합니다: {csv_path}", "WARN")

    link = row["파일 경로 링크"]
    rows = [item for item in rows if item.get("파일 경로 링크") != link]
    rows.append(row)

    # 매 다운로드마다 원자적으로 교체하여 중간 중단에도 완성된 행만 남긴다.
    temp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(csv_path)


def ensure_csv(csv_path: Path):
    """다운로드 결과가 아직 없어도 헤더가 있는 CSV를 생성한다."""
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
            csv.DictWriter(fp, fieldnames=CSV_HEADERS).writeheader()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lstrip("'")).casefold()


def deck_key(value: str) -> str:
    """썸네일 URL 또는 ZIP 파일명에서 템플릿 고유 slug를 얻는다."""
    value = unquote(str(value or "")).replace("\\", "/")
    match = re.search(r"/([^/]+)/thumbnails?/", value, re.I)
    if match:
        return match.group(1).casefold()
    name = Path(value.rsplit("/", 1)[-1]).stem
    return re.sub(r"_\d+$", "", name).casefold()


def file_path_from_link(link: str):
    try:
        parsed = urlparse(str(link or ""))
        if parsed.scheme.lower() == "file":
            raw = unquote(parsed.path)
            if parsed.netloc:
                raw = f"//{parsed.netloc}{raw}"
            if re.match(r"^/[A-Za-z]:/", raw):
                raw = raw[1:]
            return Path(raw).resolve()
        if link:
            return Path(unquote(str(link))).resolve()
    except Exception:
        pass
    return None


def empty_csv_index():
    return {"titles": set(), "thumbnail_urls": set(), "deck_keys": set(),
            "filenames": set(), "count": 0}


def add_csv_index_entry(index: dict, thumbnail_url: str, title: str, target: Path):
    index["titles"].add(normalize_text(title))
    index["thumbnail_urls"].add(str(thumbnail_url or "").strip())
    index["deck_keys"].update(filter(None, [deck_key(thumbnail_url), deck_key(target.name)]))
    index["filenames"].add(target.name.casefold())


def load_csv_download_index(csv_path: Path) -> dict:
    """CSV 행 중 실제 ZIP 파일이 존재하는 항목만 다운로드 완료로 인덱싱한다."""
    index = empty_csv_index()
    if not csv_path.exists():
        return index
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
            for row in csv.DictReader(fp):
                target = file_path_from_link(row.get("파일 경로 링크", ""))
                if target is None or not target.is_file():
                    continue
                add_csv_index_entry(
                    index,
                    row.get("썸네일 이미지", ""),
                    row.get("썸네일 제목", ""),
                    target,
                )
                index["count"] += 1
    except Exception as error:
        log(f"CSV 완료 목록을 읽지 못했습니다: {error}", "WARN")
    return index


def csv_item_downloaded(index: dict, item: dict) -> bool:
    title = normalize_text(item.get("thumbnail_title", ""))
    thumbnail_url = str(item.get("thumbnail_url", "") or "").strip()
    key = deck_key(thumbnail_url)
    return bool(
        (title and title in index["titles"])
        or (thumbnail_url and thumbnail_url in index["thumbnail_urls"])
        or (key and key in index["deck_keys"])
    )


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


def done_filename(manifest: dict, category: str, key: str):
    for entry in manifest.get(category, []):
        if entry.get("key") == key and entry.get("status") == "ok":
            return entry.get("filename")
    return None


def existing_done_keys(manifest: dict, category: str, cat_dir: Path) -> set:
    """manifest가 성공이어도 실제 파일이 남아 있는 항목만 완료로 본다."""
    return {
        entry["key"]
        for entry in manifest.get(category, [])
        if entry.get("status") == "ok"
        and entry.get("filename")
        and (cat_dir / entry["filename"]).is_file()
    }


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
def detect_category_labels(page, visible_only=False):
    """현재 카테고리 캐러셀의 라벨을 왼쪽부터 수집한다."""
    labels = []

    # Genspark 현재 DOM. 캐러셀 바깥으로 잘린 항목은 다음 페이지에서 처리한다.
    try:
        items = page.locator(CATEGORY_ITEM_SELECTOR)
        wrap = page.locator(CATEGORY_WRAP_SELECTOR).first
        wrap_box = wrap.bounding_box() if wrap.count() else None
        visible_left = wrap_box["x"] if wrap_box else None
        visible_right = wrap_box["x"] + wrap_box["width"] if wrap_box else None
        for arrow_selector, edge in [
            (".ds-cat-tabs-arrow--left", "left"),
            (CATEGORY_NEXT_SELECTOR, "right"),
        ]:
            arrow = page.locator(arrow_selector).first
            if not wrap_box or not arrow.count() or not arrow.is_visible():
                continue
            arrow_box = arrow.bounding_box()
            if not arrow_box:
                continue
            if edge == "left":
                visible_left = max(visible_left, arrow_box["x"] + arrow_box["width"])
            else:
                visible_right = min(visible_right, arrow_box["x"])
        for i in range(items.count()):
            item = items.nth(i)
            name = item.locator(CATEGORY_NAME_SELECTOR)
            text = ((name.first.inner_text() if name.count() else item.inner_text()) or "").strip()
            if not text or text in CATEGORY_EXCLUDED_LABELS or text in labels:
                continue
            if visible_only:
                box = item.bounding_box()
                if not box or not item.is_visible():
                    continue
                if wrap_box:
                    if box["x"] < visible_left or box["x"] + box["width"] > visible_right:
                        continue
            labels.append(text)
    except Exception:
        pass

    if labels:
        return labels

    # 구조 변경 시 접근성 role을 폴백으로 사용한다.
    try:
        tabs = page.get_by_role("tab")
        for i in range(tabs.count()):
            tab = tabs.nth(i)
            text = (tab.inner_text() or "").strip()
            if ((not visible_only or tab.is_visible())
                    and text
                    and text not in CATEGORY_EXCLUDED_LABELS
                    and text not in labels):
                labels.append(text)
    except Exception:
        pass
    return labels


def explicit_categories(args):
    if args.categories and args.categories.strip().lower() != "all":
        return [c.strip() for c in args.categories.split(",") if c.strip()]
    return None


def click_category(page, category):
    """카테고리 탭을 클릭한다. 여러 로케이터 전략을 순차 시도."""
    candidates = [
        lambda: page.locator(CATEGORY_ITEM_SELECTOR).filter(
            has_text=re.compile(rf"^\s*{re.escape(category)}\s*$")),
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


def category_is_visible(page, category):
    return category in detect_category_labels(page, visible_only=True)


def click_category_arrow(page, direction, args):
    """카테고리 캐러셀 화살표를 누르고 실제 수평 이동 여부를 반환한다."""
    selector = (args.category_next_selector if direction == "right"
                else ".ds-cat-tabs-arrow--left")
    arrows = page.locator(selector)
    arrow = None
    for i in range(arrows.count()):
        try:
            if arrows.nth(i).is_visible() and arrows.nth(i).is_enabled():
                arrow = arrows.nth(i)
                break
        except Exception:
            continue
    if arrow is None:
        return False

    try:
        arrow.scroll_into_view_if_needed(timeout=3000)
        tabs = page.locator(CATEGORY_TABS_SELECTOR).first
        before_scroll = tabs.evaluate("el => el.scrollLeft") if tabs.count() else None
        before_labels = detect_category_labels(page, visible_only=True)
        try:
            arrow.click(timeout=4000)
        except Exception:
            arrow.click(timeout=4000, force=True)
        page.wait_for_timeout(args.category_scroll_pause_ms)
        after_scroll = tabs.evaluate("el => el.scrollLeft") if tabs.count() else None
        after_labels = detect_category_labels(page, visible_only=True)
        moved = before_scroll != after_scroll or before_labels != after_labels
        if moved:
            log(f"카테고리 {direction} 이동: {after_labels}")
        return moved
    except Exception as error:
        log(f"카테고리 {direction} 화살표 클릭 실패: {error}", "WARN")
        return False


def reveal_category(page, category, args):
    """숨겨진 카테고리를 오른쪽 화살표로 노출한다."""
    if category_is_visible(page, category):
        return True
    for _ in range(args.max_category_pages):
        if not click_category_arrow(page, "right", args):
            break
        if category_is_visible(page, category):
            return True
    return category_is_visible(page, category)


# ============================================================
# 카드 탐지 / 그리드 로딩
# ============================================================
def detect_card_selector(page):
    """카드 후보 셀렉터 중 가장 적합한 것을 선택한다."""
    for sel in CARD_CANDIDATE_SELECTORS:
        try:
            loc = page.locator(sel)
            cnt = loc.count()
            if cnt < 1:
                continue
            # 후보 순서 자체가 구체성 순서다. "가장 많이 매칭되는" 후보를
            # 고르면 카드 내부 캐러셀 슬라이드가 이겨 버리므로 사용하지 않는다.
            sample = min(cnt, 8)
            card_sized = 0
            for i in range(sample):
                box = loc.nth(i).bounding_box()
                if box and box["width"] >= 180 and box["height"] >= 120:
                    card_sized += 1
            if card_sized >= max(1, sample // 2):
                return sel
        except Exception:
            continue
    return None


def card_metadata(card, fallback=""):
    """카드 식별자와 CSV에 기록할 썸네일 정보를 함께 수집한다."""
    try:
        value = card.evaluate("""
            el => {
                const stableImg = el.querySelector('img');
                const img = el.querySelector('.ds-card-thumb-slide.is-active img') || stableImg;
                const title = el.querySelector('.ds-card-title, h1, h2, h3, h4');
                const link = el.matches('a') ? el : el.querySelector('a');
                const dataId = el.getAttribute('data-id') ||
                    el.getAttribute('data-skill-id') ||
                    el.getAttribute('data-template-id') || '';
                const titleText = title ? (title.textContent || '').trim() : '';
                const imageUrl = img ? (img.currentSrc || img.getAttribute('src') || '') : '';
                const imageAlt = img ? (img.getAttribute('alt') || '') : '';
                const stableImageUrl = stableImg ? (stableImg.getAttribute('src') || '') : '';
                const key = [
                    dataId,
                    link ? (link.getAttribute('href') || '') : '',
                    titleText,
                    stableImageUrl,
                    imageAlt
                ].join('|');
                return {
                    key,
                    thumbnailUrl: imageUrl,
                    thumbnailTitle: titleText || imageAlt
                };
            }
        """)
        if value and value.get("key", "").strip("|"):
            return value
    except Exception:
        pass
    return {"key": fallback, "thumbnailUrl": "", "thumbnailTitle": ""}


def card_identity(card, fallback=""):
    """모달을 닫은 뒤에도 같은 카드를 다시 찾기 위한 안정적인 UI 식별자."""
    return card_metadata(card, fallback)["key"]


def visible_card_rows(page, card_selector, seen):
    """현재 화면의 카드들을 y 좌표로 행 그룹화하고 각 행은 x 좌표로 정렬한다."""
    viewport_h = page.evaluate("window.innerHeight")
    cards = page.locator(card_selector)
    visible = []
    for i in range(cards.count()):
        try:
            card = cards.nth(i)
            box = card.bounding_box()
            if not box or box["width"] < 180 or box["height"] < 120:
                continue
            if box["y"] + box["height"] <= 0 or box["y"] >= viewport_h:
                continue
            metadata = card_metadata(card, f"index:{i}")
            key = metadata["key"]
            if key in seen:
                continue
            visible.append({"key": key, "x": box["x"], "y": box["y"],
                            "height": box["height"],
                            "thumbnail_url": metadata["thumbnailUrl"],
                            "thumbnail_title": metadata["thumbnailTitle"]})
        except Exception:
            continue

    visible.sort(key=lambda item: (item["y"], item["x"]))
    rows = []
    for item in visible:
        if not rows:
            rows.append([item])
            continue
        anchor_y = sum(x["y"] for x in rows[-1]) / len(rows[-1])
        tolerance = max(24, min(x["height"] for x in rows[-1]) * 0.25)
        if abs(item["y"] - anchor_y) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
    for row in rows:
        row.sort(key=lambda item: item["x"])
    return rows


def find_card_by_identity(page, card_selector, key):
    """SPA 재렌더 뒤 인덱스가 바뀌어도 카드 식별자로 다시 바인딩한다."""
    cards = page.locator(card_selector)
    for i in range(cards.count()):
        try:
            card = cards.nth(i)
            if card_identity(card, f"index:{i}") == key:
                return card
        except Exception:
            continue
    return None


def scroll_one_row(page, row, args):
    """처리한 카드 행의 높이만큼만 내려 다음 행을 화면에 올린다."""
    row_height = max(item["height"] for item in row)
    step = max(180, int(row_height + args.row_gap))
    before = page.evaluate("window.scrollY")
    page.evaluate("dy => window.scrollBy(0, dy)", step)
    page.wait_for_timeout(args.scroll_pause_ms)
    after = page.evaluate("window.scrollY")
    return after > before


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
    # 다운로드 후 모달이 자동으로 닫힌 경우 페이지의 무관한 'close' 요소를
    # 잘못 클릭하지 않는다.
    if modal is None and _find_download_button(page) is None:
        return
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


def click_card_thumbnail(card_locator, timeout_ms):
    """움직이는 캐러셀 컨테이너 대신 실제 썸네일 이미지를 클릭한다."""
    card_locator.scroll_into_view_if_needed(timeout=4000)
    images = card_locator.locator("img:visible")
    if images.count() > 0:
        try:
            images.first.click(timeout=timeout_ms)
            return
        except Exception:
            # 자동 전환 중인 캐러셀은 stability 검사가 계속 실패할 수 있다.
            images.first.click(timeout=timeout_ms, force=True)
            return
    card_locator.click(timeout=timeout_ms, force=True)


def download_one(page, card_locator, index, cat_dir: Path, already, args):
    """카드 1개를 열고, 미완료 항목일 때만 zip을 저장한다."""
    click_card_thumbnail(card_locator, args.element_timeout * 1000)

    if not wait_modal_open(page):
        close_modal(page)
        raise RuntimeError("모달이 열리지 않음")

    title = get_modal_title(page) or f"item_{index+1}"
    dedup_key = title

    # 기존 구현은 중복 여부를 다운로드한 뒤 확인했다. 모달 제목을 얻은 직후
    # 확인하면 이어받기 실행에서 불필요한 첨부파일 다운로드가 발생하지 않는다.
    if normalize_text(dedup_key) in already:
        close_modal(page)
        return None, dedup_key, False

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
    return target.name, dedup_key, True


# ============================================================
# 카테고리 처리
# ============================================================
def process_category(page, category, out_root: Path, csv_path: Path,
                     csv_index: dict, manifest: dict, args):
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

    cat_dir = out_root / sanitize_name(category)
    cat_dir.mkdir(parents=True, exist_ok=True)

    already = ({normalize_text(key) for key in existing_done_keys(manifest, category, cat_dir)}
               if not args.no_resume else set())
    # CSV는 --no-resume 여부와 무관하게 실제 파일이 존재하면 항상 완료로 취급한다.
    already.update(csv_index["titles"])
    processed = 0

    # 첫 행부터 시작한다. 이후에는 행 높이만큼만 스크롤하며, 현재 화면에서
    # y 좌표가 같은 카드들을 왼쪽(x가 작은 카드)부터 처리한다.
    cards = page.locator(card_selector)
    if cards.count() == 0:
        log("카드가 0개입니다.", "WARN")
        return
    cards.first.scroll_into_view_if_needed(timeout=4000)
    page.evaluate("window.scrollBy(0, -100)")
    page.wait_for_timeout(args.scroll_pause_ms)

    seen = set()
    scroll_count = 0
    idle_scrolls = 0
    item_no = 0

    while scroll_count <= args.max_scrolls:
        if args.limit and processed >= args.limit:
            log(f"--limit {args.limit} 도달, 다음 카테고리로 이동")
            break

        rows = visible_card_rows(page, card_selector, seen)
        if not rows:
            before = page.evaluate("window.scrollY")
            page.evaluate("window.scrollBy(0, Math.max(240, window.innerHeight * 0.65))")
            page.wait_for_timeout(args.scroll_pause_ms)
            after = page.evaluate("window.scrollY")
            scroll_count += 1
            idle_scrolls += 1
            if after == before or idle_scrolls >= 3:
                break
            continue

        idle_scrolls = 0
        row = rows[0]
        log(f"화면의 다음 행 처리: {len(row)}개 (왼쪽 -> 오른쪽)")

        for item in row:
            if args.limit and processed >= args.limit:
                break
            item_no += 1
            seen.add(item["key"])

            if csv_item_downloaded(csv_index, item):
                label = item["thumbnail_title"] or deck_key(item["thumbnail_url"]) or "알 수 없는 템플릿"
                log(f"[{item_no}] CSV에서 완료 확인: {label} (카드 클릭/다운로드 생략)")
                continue

            card = find_card_by_identity(page, card_selector, item["key"])
            if card is None:
                log(f"[{item_no}] SPA 갱신 후 카드를 다시 찾지 못해 건너뜀", "WARN")
                continue
            try:
                fname, key, downloaded = download_one(
                    page, card, item_no - 1, cat_dir, already, args)
                if downloaded:
                    record(manifest, category, key, fname, "ok")
                    already.add(normalize_text(key))
                    processed += 1
                    log(f"[{item_no}] 저장 완료: {fname}  <-  {key}")
                else:
                    fname = done_filename(manifest, category, key)
                    log(f"[{item_no}] 이미 완료된 항목: {key} (다운로드 생략)")

                if fname:
                    try:
                        write_csv_entry(
                            csv_path,
                            item["thumbnail_url"],
                            item["thumbnail_title"] or key,
                            cat_dir / fname,
                        )
                        target = (cat_dir / fname).resolve()
                        add_csv_index_entry(
                            csv_index,
                            item["thumbnail_url"],
                            item["thumbnail_title"] or key,
                            target,
                        )
                        csv_index["count"] += 1
                    except Exception as csv_error:
                        log(f"[{item_no}] CSV 기록 실패: {csv_error}", "WARN")
            except Exception as e:
                error_id = hashlib.sha1(item["key"].encode("utf-8")).hexdigest()[:12]
                log(f"[{item_no}] 실패: {e}", "ERROR")
                record(manifest, category, f"error_{error_id}", None, f"error: {e}")
                close_modal(page)

            save_manifest(out_root, manifest)
            page.wait_for_timeout(int(args.delay * 1000))

        if args.limit and processed >= args.limit:
            continue
        moved = scroll_one_row(page, row, args)
        scroll_count += 1
        if not moved:
            # 마지막 행일 수 있으므로 한 번 더 현재 화면의 미처리 카드를 확인한다.
            if not visible_card_rows(page, card_selector, seen):
                break

    log(f"카테고리 '{category}' 완료: 이번 실행에서 {processed}개 신규 저장")


def run_category_collection(page, out_root: Path, csv_path: Path,
                            csv_index: dict, manifest: dict, args):
    """현재 분류들을 처리한 뒤 오른쪽 화살표로 다음 분류 묶음을 순회한다."""
    requested = explicit_categories(args)

    def process_one(category):
        try:
            process_category(
                page, category, out_root, csv_path, csv_index, manifest, args)
        except Exception as error:
            log(f"카테고리 처리 중 예외: {category} -> {error}", "ERROR")
        finally:
            save_manifest(out_root, manifest)

    if requested is not None:
        log(f"지정된 카테고리({len(requested)}): {requested}")
        for category in requested:
            if not category_is_visible(page, category):
                log(f"숨겨진 카테고리 노출 시도: {category}")
                reveal_category(page, category, args)
            process_one(category)
        return

    processed_categories = set()
    seen_pages = set()

    for page_no in range(1, args.max_category_pages + 1):
        visible = detect_category_labels(page, visible_only=True)
        if not visible and page_no == 1:
            log("카테고리 DOM 자동 감지 실패 -> 기본 목록으로 처리", "WARN")
            for category in DEFAULT_CATEGORIES:
                if not category_is_visible(page, category):
                    reveal_category(page, category, args)
                process_one(category)
            return

        state = tuple(visible)
        new_categories = [name for name in visible if name not in processed_categories]
        log(f"카테고리 캐러셀 {page_no}페이지: {visible}")

        for category in new_categories:
            processed_categories.add(category)
            process_one(category)

        if state in seen_pages and not new_categories:
            log("카테고리 캐러셀 위치가 반복되어 순회를 종료합니다.")
            break
        seen_pages.add(state)

        # 현재 화면 분류를 모두 처리한 다음에만 오른쪽으로 이동한다.
        if not click_category_arrow(page, "right", args):
            log("카테고리 오른쪽 끝에 도달했습니다.")
            break
    else:
        log(f"카테고리 캐러셀 최대 {args.max_category_pages}페이지에 도달했습니다.", "WARN")

    log(f"분류 수집 완료: {len(processed_categories)}개 카테고리")


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
    visible_labels = detect_category_labels(page, visible_only=True)
    arrow = page.locator(CATEGORY_NEXT_SELECTOR)
    arrow_visible = False
    for i in range(arrow.count()):
        try:
            if arrow.nth(i).is_visible():
                arrow_visible = True
                break
        except Exception:
            continue
    log(f"감지된 전체 분류({len(labels)}): {labels}")
    log(f"현재 노출된 분류({len(visible_labels)}): {visible_labels}")
    log(f"오른쪽 분류 화살표: count={arrow.count()}, visible={arrow_visible}")

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
    ap.add_argument("--csv", default="slides.csv",
                    help="결과 CSV 경로. 상대 경로는 --output 기준 (기본: slides.csv)")
    ap.add_argument("--profile", default=".gsprofile",
                    help="브라우저 프로필 폴더 (로그인 세션 유지)")
    ap.add_argument("--categories", default="",
                    help="대상 카테고리 콤마 구분. 'all' 또는 미지정=화살표를 포함해 전체 자동 순회")
    ap.add_argument("--limit", type=int, default=0,
                    help="카테고리당 최대 다운로드 수 (0=무제한, 테스트 시 3 권장)")
    ap.add_argument("--card-selector", default="",
                    help="카드 CSS 셀렉터 강제 지정(자동 탐지 실패 시)")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="카드 간 지연(초). 서버 보호용")
    ap.add_argument("--scroll-pause-ms", type=int, default=900,
                    help="행 스크롤 후 lazy-load 대기(ms)")
    ap.add_argument("--row-gap", type=int, default=36,
                    help="한 행 처리 후 추가 스크롤 간격(px, 기본: 36)")
    ap.add_argument("--max-scrolls", type=int, default=60,
                    help="카테고리별 최대 행 스크롤 횟수")
    ap.add_argument("--category-next-selector", default=CATEGORY_NEXT_SELECTOR,
                    help="분류 캐러셀 오른쪽 화살표 CSS 셀렉터")
    ap.add_argument("--max-category-pages", type=int, default=20,
                    help="분류 캐러셀 최대 오른쪽 이동 페이지 수")
    ap.add_argument("--category-scroll-pause-ms", type=int, default=700,
                    help="분류 캐러셀 화살표 클릭 후 대기(ms)")
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
                    help="manifest만 무시(CSV에 존재하고 실제 파일이 있는 항목은 계속 생략)")
    ap.add_argument("--inspect", action="store_true",
                    help="셀렉터 점검(스크린샷+접근성 트리+카드 후보 리포트) 후 종료")
    return ap.parse_args()


def main():
    args = parse_args()
    out_root = Path(args.output).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    csv_path = resolve_csv_path(out_root, args.csv)
    ensure_csv(csv_path)
    csv_index = load_csv_download_index(csv_path)
    manifest = load_manifest(out_root)

    log(f"저장 루트: {out_root}")
    log(f"결과 CSV: {csv_path}")
    log(f"CSV에서 확인된 기존 다운로드: {csv_index['count']}개")
    log(f"프로필: {Path(args.profile).resolve()}")

    with sync_playwright() as p:
        context = launch_context(p, args)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_logged_in(page, args)

            if args.inspect:
                run_inspect(page, out_root)
                return

            run_category_collection(
                page, out_root, csv_path, csv_index, manifest, args)

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
