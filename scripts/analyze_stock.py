#!/usr/bin/env python3
"""AI 데이터센터 밸류체인 저평가 종목 탐색기 — 핵심 분석 로직.

3개 섹션(발전·전력공급, 전력 장비·인프라, 국내 중전기)
전 종목의 [PER, PBR, EV/EBITDA, ROIC, PEG]를 수집/계산하고,
섹션 내 동종업계 평균 대비 저평가 종목을 선별해
'오늘의 AI 밸류체인 저평가 Top 10' Hugo 포스트를 생성한다.

데이터 소스
  - 국내(.KS/.KQ, source="dart"): DART 공시 최신 분기 보고서 우선 + 시장가
  - 해외(source="yahoo"): Yahoo Finance

동작 모드
  --mode sample : data/sample_data.json 으로 오프라인 분석(API 키 불필요, 예시/CI 검증용)
  --mode live   : DART_API_KEY + yfinance 로 실데이터 수집(기본)

사용 예
  python scripts/analyze_stock.py --mode sample --date 2026-06-02
  python scripts/analyze_stock.py --mode live
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = ROOT / "data" / "sample_data.json"
POSTS_DIR = ROOT / "content" / "posts"   # Hugo 콘텐츠 디렉터리
LATEST_PATH = ROOT / "static" / "data" / "latest.json"  # 메인 대시보드용
HISTORY_PATH = ROOT / "static" / "data" / "history.json"  # 일별 Top 10 스냅샷 누적
KST = timezone(timedelta(hours=9))

SECTION_ORDER = [
    "전력 인프라/장비",
    "에너지·원전",
    "냉각 기술",
    "광통신",
    "CPU/NPU",
    "SSD/메모리",
]

# 섹터 메타(아이콘·URL slug·한 줄 설명) — 전체 페이지/섹터 상세 페이지에서 사용.
SECTOR_META: dict[str, dict] = {
    "전력 인프라/장비": {"emoji": "⚡", "slug": "power-grid",
                   "desc": "송전·변압 등 전력망 인프라와 중전기 장비. AI 데이터센터 전력 수요의 1차 수혜."},
    "에너지·원전":     {"emoji": "🔋", "slug": "energy-nuclear",
                   "desc": "데이터센터에 전기를 공급하는 발전원 — 원자력·SMR·대형 발전사."},
    "냉각 기술":       {"emoji": "🌡️", "slug": "cooling",
                   "desc": "고발열 AI 서버를 식히는 액체냉각·열관리 솔루션."},
    "광통신":         {"emoji": "🔦", "slug": "optical",
                   "desc": "AI 서버·데이터센터를 잇는 초고속 광통신 부품·장비."},
    "CPU/NPU":        {"emoji": "🖥️", "slug": "compute",
                   "desc": "AI 연산의 두뇌 — CPU·GPU·AI 가속기(NPU)."},
    "SSD/메모리":     {"emoji": "💾", "slug": "memory",
                   "desc": "AI 데이터 저장·고용량 메모리(SSD·NAND)."},
}

# 분석 유니버스(섹션별 종목). live 모드의 수집 대상이기도 하다.
# 국내 종목은 corp_code 미지정 — DART 마스터에서 자동 해석(fetch_dart).
# 겹치는 종목은 주력 섹터 1곳에만 배치(버티브→냉각, 컨스텔레이션·GE버노바→에너지).
UNIVERSE: dict[str, list[dict]] = {
    "전력 인프라/장비": [
        {"name": "콴타 서비시스", "ticker": "PWR", "source": "yahoo"},
        {"name": "효성중공업", "ticker": "298040.KS", "source": "dart"},
        {"name": "HD현대일렉트릭", "ticker": "267260.KS", "source": "dart"},
        {"name": "LS일렉트릭", "ticker": "010120.KS", "source": "dart"},
    ],
    "에너지·원전": [
        {"name": "컨스텔레이션 에너지", "ticker": "CEG", "source": "yahoo"},
        {"name": "GE 버노바", "ticker": "GEV", "source": "yahoo"},
        {"name": "비스트라", "ticker": "VST", "source": "yahoo"},
        {"name": "탈렌 에너지", "ticker": "TLN", "source": "yahoo"},
        {"name": "BWX 테크놀로지스", "ticker": "BWXT", "source": "yahoo"},
        {"name": "뉴스케일 파워", "ticker": "SMR", "source": "yahoo"},
        {"name": "오클로", "ticker": "OKLO", "source": "yahoo"},
        {"name": "나노 뉴클리어", "ticker": "NNE", "source": "yahoo"},
        {"name": "두산에너빌리티", "ticker": "034020.KS", "source": "dart"},
    ],
    "냉각 기술": [
        {"name": "버티브", "ticker": "VRT", "source": "yahoo"},
        {"name": "모딘", "ticker": "MOD", "source": "yahoo"},
        {"name": "알파라발", "ticker": "ALFA.ST", "source": "yahoo"},
        {"name": "다이킨", "ticker": "6367.T", "source": "yahoo"},
    ],
    "광통신": [
        {"name": "코히어런트", "ticker": "COHR", "source": "yahoo"},
        {"name": "루멘텀", "ticker": "LITE", "source": "yahoo"},
        {"name": "코닝", "ticker": "GLW", "source": "yahoo"},
        {"name": "시에나", "ticker": "CIEN", "source": "yahoo"},
        {"name": "아리스타 네트웍스", "ticker": "ANET", "source": "yahoo"},
    ],
    "CPU/NPU": [
        {"name": "AMD", "ticker": "AMD", "source": "yahoo"},
        {"name": "인텔", "ticker": "INTC", "source": "yahoo"},
        {"name": "Arm 홀딩스", "ticker": "ARM", "source": "yahoo"},
        {"name": "엔비디아", "ticker": "NVDA", "source": "yahoo"},
        {"name": "브로드컴", "ticker": "AVGO", "source": "yahoo"},
        {"name": "퀄컴", "ticker": "QCOM", "source": "yahoo"},
    ],
    "SSD/메모리": [
        {"name": "샌디스크", "ticker": "SNDK", "source": "yahoo"},
        {"name": "키옥시아", "ticker": "285A.T", "source": "yahoo"},
    ],
}

VALUATION_METRICS = ["per", "pbr", "ev_ebitda"]  # 낮을수록 저평가
METRIC_LABEL = {
    "per": "PER",
    "pbr": "PBR",
    "ev_ebitda": "EV/EBITDA",
    "roic": "ROIC(%)",
    "peg": "PEG",
}


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass
class Stock:
    name: str
    ticker: str
    source: str
    section: str
    currency: str = ""
    market_cap: Optional[float] = None
    per: Optional[float] = None
    pbr: Optional[float] = None
    ev_ebitda: Optional[float] = None
    roic: Optional[float] = None  # %
    peg: Optional[float] = None
    # 분석 결과
    value_score: float = 0.0       # 동종평균 대비 멀티플 할인폭(섹터 가중)
    quality_bonus: float = 0.0     # ROIC 가점
    growth_bonus: float = 0.0      # PEG<1 가점
    safety_bonus: float = 0.0      # 안전마진(PBR<1, EV/EBITDA<평균) 가점
    safety_flags: list[str] = field(default_factory=list)
    momentum: Optional[float] = None  # 최근 1년 주가 수익률(%)
    trend_warn: bool = False          # 하락 추세(가치 함정) 경고
    forward_pe: Optional[float] = None  # 추정 PER(선행)
    forward_score: float = 0.0          # 추정치(이익전망·목표가·투자의견) 신호
    composite: float = 0.0
    is_candidate: bool = False     # 1차 후보군 여부
    cheap_flags: list[str] = field(default_factory=list)
    history: Optional[dict] = None  # {"dates": [...], "closes": [...]} 최근 3년 월봉
    supply: Optional[dict] = None   # 수급: {dates, foreign, organ, individual, foreignHold} 최근 10일
    last_close: Optional[dict] = None  # 전일 종가: {price, pct, dir, date, cur}
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    target_price: Optional[float] = None   # 목표주가(컨센서스)
    recomm_mean: Optional[float] = None     # 투자의견 평균(1매수~5매도)
    volume: Optional[float] = None          # 거래량
    ai_universe: bool = True                # AI 4개 섹션 분석 대상 여부
    industry: Optional[str] = None          # KRX/KSIC 업종(FDR KRX-DESC)
    income_trend: Optional[dict] = None     # 최근 3년 당기순이익 {years, values(억원), trend}

    def metric(self, key: str) -> Optional[float]:
        return getattr(self, key)


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------
def _last_months(n: int) -> list[str]:
    """현재 월부터 거꾸로 n개월의 'YYYY-MM' 레이블(과거→현재 순)."""
    d = datetime.now(KST).date().replace(day=1)
    y, m, out = d.year, d.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _last_days(n: int) -> list[str]:
    """현재부터 거꾸로 n일의 'MM-DD' 레이블(과거→현재 순)."""
    d = datetime.now(KST).date()
    return list(reversed([(d - timedelta(days=i)).strftime("%m-%d") for i in range(n)]))


def _is_kr(ticker: str) -> bool:
    return ticker.endswith((".KS", ".KQ"))


def synth_history(stock: Stock) -> None:
    """sample 모드용 결정론적(가짜) 3년 월봉 — 예시 차트 시연용."""
    import math
    seed = sum(ord(c) for c in stock.name)
    n = 36
    level = 40 + seed % 60
    closes = []
    for i in range(n):
        trend = level * (1 + 0.012 * i)
        wave = level * 0.08 * math.sin(((i + seed) % 12) / 12 * 2 * math.pi)
        closes.append(round(trend + wave, 2))
    stock.history = {"dates": _last_months(n), "closes": closes}


def synth_supply(stock: Stock) -> None:
    """sample 모드용 결정론적(가짜) 수급(국내 종목만) — 예시 시연용."""
    if not _is_kr(stock.ticker):
        return
    import math
    seed = sum(ord(c) for c in stock.name)
    n = 10
    foreign, organ, indiv = [], [], []
    for i in range(n):
        f = int(1000 * (1 + seed % 5) * math.sin(((i + seed) % 7) / 7 * 2 * math.pi))
        o = int(800 * (1 + seed % 4) * math.cos(((i + seed) % 5) / 5 * 2 * math.pi))
        foreign.append(f)
        organ.append(o)
        indiv.append(-(f + o))
    stock.supply = {"dates": _last_days(n), "foreign": foreign, "organ": organ,
                    "individual": indiv, "foreignHold": f"{30 + seed % 30}.0%"}


def _num_prefix(s) -> Optional[float]:
    """'29.14배', '377,000' 같은 문자열에서 숫자만 추출."""
    import re
    if s is None:
        return None
    m = re.search(r"-?[\d,]+\.?\d*", str(s))
    return _to_num(m.group()) if m else None


def fetch_quote(stock: Stock) -> None:
    """목표주가·52주 고저·거래량·투자의견 — 국내=네이버 통합, 해외=Yahoo."""
    try:
        if _is_kr(stock.ticker):
            import requests
            code = stock.ticker.split(".")[0]
            j = requests.get(
                f"https://m.stock.naver.com/api/stock/{code}/integration",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"},
                timeout=12).json()
            ti = {it.get("key"): it.get("value") for it in j.get("totalInfos", [])}
            stock.week52_high = _num_prefix(ti.get("52주 최고"))
            stock.week52_low = _num_prefix(ti.get("52주 최저"))
            if stock.volume is None:
                stock.volume = _num_prefix(ti.get("거래량"))
            if stock.per is None:
                stock.per = _num_prefix(ti.get("PER"))
            if stock.pbr is None:
                stock.pbr = _num_prefix(ti.get("PBR"))
            stock.forward_pe = _num_prefix(ti.get("추정PER"))
            ci = j.get("consensusInfo") or {}
            stock.target_price = _num_prefix(ci.get("priceTargetMean"))
            stock.recomm_mean = _num_prefix(ci.get("recommMean"))
        else:
            import yfinance as yf
            i = yf.Ticker(stock.ticker).info
            stock.week52_high = _clean(i.get("fiftyTwoWeekHigh"))
            stock.week52_low = _clean(i.get("fiftyTwoWeekLow"))
            stock.volume = _clean(i.get("regularMarketVolume") or i.get("volume"))
            stock.target_price = _clean(i.get("targetMeanPrice"))
            stock.recomm_mean = _clean(i.get("recommendationMean"))
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] {stock.name} 시세지표 수집 실패: {e}", file=sys.stderr)


_CORP_MAP: dict[str, str] = {}
_CORP_LOADED = False


def _load_corp_map() -> None:
    """DART corpCode 마스터에서 종목코드(6자리)→corp_code 매핑 1회 적재."""
    global _CORP_LOADED
    if _CORP_LOADED:
        return
    _CORP_LOADED = True
    key = os.environ.get("DART_API_KEY")
    if not key:
        return
    try:
        import requests, io, zipfile
        import xml.etree.ElementTree as ET
        z = requests.get("https://opendart.fss.or.kr/api/corpCode.xml",
                         params={"crtfc_key": key}, timeout=60).content
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(z)).read("CORPCODE.xml").decode("utf-8"))
        for c in root.iter("list"):
            sc = (c.findtext("stock_code") or "").strip()
            if sc:
                _CORP_MAP[sc] = c.findtext("corp_code")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] corpCode 매핑 로드 실패: {e}", file=sys.stderr)


def fetch_income_trend(code6: str) -> Optional[dict]:
    """최근 3년 당기순이익(억원) 추세 — DART 연간 보고서 1회 호출."""
    key = os.environ.get("DART_API_KEY")
    if not key:
        return None
    _load_corp_map()
    corp = _CORP_MAP.get(code6)
    if not corp:
        return None
    import requests
    cur_year = int(datetime.now(KST).strftime("%Y"))
    for y in (cur_year - 1, cur_year - 2):  # 최근 확정 사업연도
        try:
            r = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
                             params={"crtfc_key": key, "corp_code": corp, "bsns_year": str(y),
                                     "reprt_code": "11011", "fs_div": "CFS"}, timeout=15).json()
            if r.get("status") != "000":
                continue
            ni = next((it for it in r.get("list", [])
                       if it.get("sj_div") == "IS" and "당기순이익" in (it.get("account_nm") or "")), None)
            if not ni:
                continue
            vals = [_to_num(ni.get("bfefrmtrm_amount")), _to_num(ni.get("frmtrm_amount")),
                    _to_num(ni.get("thstrm_amount"))]
            if any(v is None for v in vals):
                continue
            if vals[2] > vals[1] > vals[0]:
                trend = "up"
            elif vals[2] < vals[1] < vals[0]:
                trend = "down"
            else:
                trend = "mixed"
            return {"years": [str(y - 2), str(y - 1), str(y)],
                    "values": [round(v / 1e8) for v in vals], "trend": trend}
        except Exception:  # noqa: BLE001
            continue
    return None


def enrich_income_trends(dash: dict, top_n: int = 120) -> None:
    """저평가 국내 종목(시총 상위 N) + 국내 AI 종목에 3년 순이익 추세를 주입."""
    stocks = dash["stocks"]
    targets = []
    for name, s in stocks.items():
        tk = s.get("ticker", "")
        if not tk.endswith((".KS", ".KQ")):
            continue
        if s.get("ai") or s.get("level") == "value":
            targets.append((name, s))
    # 저평가 다수일 수 있으니 시총 상위 우선
    targets.sort(key=lambda x: x[1].get("marketCap") or 0, reverse=True)
    targets = targets[:top_n]
    _load_corp_map()  # corp_code 매핑 프리워밍(스레드 경쟁 방지)
    from concurrent.futures import ThreadPoolExecutor

    def one(t: tuple) -> tuple:
        name, s = t
        return name, fetch_income_trend(s["ticker"].split(".")[0])

    with ThreadPoolExecutor(max_workers=14) as ex:
        for name, tr in ex.map(one, targets):
            if tr:
                stocks[name]["incomeTrend"] = tr
    print(f"  [info] 3년 순이익 추세 {len(targets)}종목 병렬 처리", file=sys.stderr)


_INDUSTRY: dict[str, str] = {}
_INDUSTRY_LOADED = False


def _load_industry() -> None:
    """FDR KRX-DESC에서 종목코드→업종(KSIC) 매핑을 1회 적재."""
    global _INDUSTRY_LOADED
    if _INDUSTRY_LOADED:
        return
    _INDUSTRY_LOADED = True
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:  # noqa: BLE001
            pass
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX-DESC")
        for code, ind in zip(df["Code"], df["Industry"]):
            if isinstance(ind, str) and ind and ind.lower() != "nan":
                _INDUSTRY[str(code)] = ind
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 업종 분류 로드 실패: {e}", file=sys.stderr)


def collect_kospi(limit: Optional[int] = None, chart_top: int = 150) -> list[Stock]:
    """검색용 KOSPI 전 종목. limit=None이면 전체.

    전 종목: 기본 시세 + PER/PBR/52주/목표가(네이버). 차트는 시총 상위 chart_top만.
    """
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:  # noqa: BLE001
            pass
    out: list[Stock] = []
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KOSPI")
        df = df.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False)
        if limit:
            df = df.head(limit)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] KOSPI 리스트 수집 실패: {e}", file=sys.stderr)
        return out
    _load_industry()
    _krx_market_cap("005930")  # KRX 시총 캐시 프리워밍(스레드 경쟁 방지)
    from concurrent.futures import ThreadPoolExecutor
    rows = list(df.iterrows())

    def build_one(arg: tuple) -> Stock:
        idx, (_, r) = arg
        code = str(r["Code"])
        s = Stock(name=str(r["Name"]), ticker=f"{code}.KS", source="kospi",
                  section="KOSPI", currency="KRW", ai_universe=False,
                  industry=_INDUSTRY.get(code))
        try:
            krx_cap = _krx_market_cap(code)
            s.market_cap = round((krx_cap if krx_cap else float(r["Marcap"])) / 1e12, 2)
            s.volume = float(r["Volume"]) if r.get("Volume") == r.get("Volume") else None
            close = float(r["Close"]); chg = float(r.get("ChagesRatio") or 0)
            s.last_close = {"price": close, "pct": round(chg, 2),
                            "dir": "up" if chg > 0 else ("down" if chg < 0 else "flat"),
                            "date": "", "cur": "KRW"}
        except Exception:  # noqa: BLE001
            pass
        fetch_quote(s)                 # 전 종목 PER/PBR/52주/목표가(네이버)
        if idx < chart_top:
            fetch_history(s)           # 시총 상위만 3년 차트(FDR)
        return s

    with ThreadPoolExecutor(max_workers=16) as ex:
        out = list(ex.map(build_one, enumerate(rows)))
    print(f"  [info] KOSPI {len(out)}종목 병렬 수집 완료", file=sys.stderr)
    return out


SECTION_NEWS_QUERY = {
    "전력 인프라/장비": "전력 인프라 OR 변압기 OR 송전 OR 전력망 OR 데이터센터 전력",
    "에너지·원전": "데이터센터 전력 OR 원전 OR SMR OR 원자력 OR 발전사",
    "냉각 기술": "데이터센터 냉각 OR 액체냉각 OR 서버 냉각 OR 열관리",
    "광통신": "광통신 OR 실리콘 포토닉스 OR 광트랜시버 OR 데이터센터 네트워크",
    "CPU/NPU": "AI 반도체 OR GPU OR AI 가속기 OR 엔비디아 OR 데이터센터 CPU",
    "SSD/메모리": "AI 메모리 OR HBM OR 데이터센터 SSD OR NAND OR 낸드플래시",
}


def fetch_news(query: str, n: int = 3) -> list[dict]:
    """구글 뉴스 RSS에서 상위 n건(제목·링크·출처·날짜). 키 불필요."""
    import requests
    import xml.etree.ElementTree as ET
    from urllib.parse import quote
    try:
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        root = ET.fromstring(r.content)
        out = []
        for it in root.findall(".//item")[:n]:
            title = (it.findtext("title") or "").strip()
            src = (it.findtext("source") or "").strip()
            if src and title.endswith(" - " + src):
                title = title[: -(len(src) + 3)].strip()
            out.append({"title": title, "link": (it.findtext("link") or "").strip(),
                        "source": src, "date": (it.findtext("pubDate") or "")[:16]})
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 뉴스 수집 실패({query[:12]}): {e}", file=sys.stderr)
        return []


def synth_news(section: str) -> list[dict]:
    """sample 모드용 예시 헤드라인."""
    return [{"title": f"{section} — 예시 헤드라인 {i}", "link": "#",
             "source": "예시", "date": ""} for i in (1, 2, 3)]


def fetch_supply(stock: Stock) -> None:
    """투자자별 순매수(외국인·기관·개인) 최근 10일 — 네이버 금융(국내 종목만)."""
    if not _is_kr(stock.ticker):
        return
    try:
        import requests
        code = stock.ticker.split(".")[0]
        r = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/trend",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"},
            timeout=12,
        )
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return
        rows = rows[::-1]  # 과거→현재
        col = lambda k: [int(_to_num(x.get(k)) or 0) for x in rows]  # noqa: E731
        stock.supply = {
            "dates": [f"{x['bizdate'][4:6]}-{x['bizdate'][6:8]}" for x in rows],
            "foreign": col("foreignerPureBuyQuant"),
            "organ": col("organPureBuyQuant"),
            "individual": col("individualPureBuyQuant"),
            "foreignHold": rows[-1].get("foreignerHoldRatio", ""),
        }
        # 전일 종가(KRX 공식 KRW) — 네이버 최신 행
        # compareToPreviousClosePrice 는 부호 포함(하락 시 음수)이므로 그대로 사용
        last = rows[-1]
        price = _to_num(last.get("closePrice"))
        chg = _to_num(last.get("compareToPreviousClosePrice")) or 0
        if price:
            prev = price - chg
            pct = round(chg / prev * 100, 2) if prev else 0.0
            stock.last_close = {
                "price": price, "pct": pct,
                "dir": "up" if chg > 0 else ("down" if chg < 0 else "flat"),
                "date": f"{last['bizdate'][:4]}-{last['bizdate'][4:6]}-{last['bizdate'][6:8]}",
                "cur": "KRW",
            }
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] {stock.name} 수급 수집 실패: {e}", file=sys.stderr)


def fetch_history(stock: Stock) -> None:
    """최근 3년 월말 종가 → stock.history. 국내=FDR(KRX), 해외=Yahoo."""
    try:
        is_kr = stock.ticker.endswith((".KS", ".KQ"))
        if is_kr:
            if not os.environ.get("SSL_CERT_FILE"):
                try:
                    import certifi
                    os.environ["SSL_CERT_FILE"] = certifi.where()
                except Exception:  # noqa: BLE001
                    pass
            import FinanceDataReader as fdr
            start = (datetime.now(KST).date() - timedelta(days=365 * 3 + 15)).strftime("%Y-%m-%d")
            df = fdr.DataReader(stock.ticker.split(".")[0], start)
            s = df["Close"].resample("ME").last().dropna()
        else:
            import yfinance as yf
            h = yf.Ticker(stock.ticker).history(period="3y", interval="1mo")
            s = h["Close"].dropna()
        dates = [d.strftime("%Y-%m") for d in s.index]
        closes = [round(float(x), 2) for x in s.values]
        if len(closes) >= 6:
            stock.history = {"dates": dates[-37:], "closes": closes[-37:]}
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] {stock.name} 차트 수집 실패: {e}", file=sys.stderr)


def load_sample() -> tuple[list[Stock], str]:
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    stocks: list[Stock] = []
    for section, rows in raw["sections"].items():
        for r in rows:
            s = Stock(
                name=r["name"], ticker=r["ticker"], source=r["source"],
                section=section, currency=r.get("currency", ""),
                market_cap=r.get("market_cap"), per=r.get("per"), pbr=r.get("pbr"),
                ev_ebitda=r.get("ev_ebitda"), roic=r.get("roic"), peg=r.get("peg"),
            )
            synth_history(s)
            synth_supply(s)
            c = s.history["closes"]
            pct = round((c[-1] - c[-2]) / c[-2] * 100, 2) if len(c) >= 2 and c[-2] else 0.0
            s.last_close = {"price": c[-1], "pct": pct,
                            "dir": "up" if pct > 0 else ("down" if pct < 0 else "flat"),
                            "date": s.history["dates"][-1], "cur": s.currency or "KRW"}
            hi, lo = max(c), min(c)
            s.week52_high, s.week52_low = round(hi, 2), round(lo, 2)
            s.target_price = round(c[-1] * 1.15, 2)
            s.recomm_mean = round(2.0 + (sum(ord(x) for x in s.name) % 20) / 10, 1)
            s.volume = float(1_000_000 + sum(ord(x) for x in s.name) * 7919 % 5_000_000)
            seed = sum(ord(x) for x in s.name)
            base = 500 + seed % 4000
            s.income_trend = {"years": ["2023", "2024", "2025"],
                              "values": [base, round(base * 1.18), round(base * 1.4)],
                              "trend": "up"}
            stocks.append(s)
    return stocks, raw.get("as_of", "")


def fetch_yahoo(name: str, ticker: str, section: str) -> Stock:
    """Yahoo Finance 기반 멀티플 수집. yfinance 미설치 시 예외."""
    import yfinance as yf  # 지연 임포트(샘플 모드에선 불필요)

    info = yf.Ticker(ticker).info
    per = info.get("trailingPE") or info.get("forwardPE")
    pbr = info.get("priceToBook")
    peg = info.get("trailingPegRatio") or info.get("pegRatio")
    ev_ebitda = info.get("enterpriseToEbitda")
    # ROIC 근사: ROE * (1 - 부채비중) 가 어려우면 returnOnEquity로 대체
    roic = info.get("returnOnCapital")
    if roic is None and info.get("returnOnEquity") is not None:
        roic = info["returnOnEquity"]
    if roic is not None:
        roic *= 100.0
    s = Stock(
        name=name, ticker=ticker, source="yahoo", section=section,
        currency=info.get("currency", "USD"),
        market_cap=(info.get("marketCap") or 0) / 1e9 or None,
        per=_clean(per), pbr=_clean(pbr), ev_ebitda=_clean(ev_ebitda),
        roic=_clean(roic), peg=_clean(peg),
    )
    s.forward_pe = _clean(info.get("forwardPE"))
    # 전일 종가 + 등락률
    price = info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")
    prev = info.get("previousClose")
    if price:
        pct = round((price - prev) / prev * 100, 2) if prev else 0.0
        s.last_close = {
            "price": round(float(price), 2), "pct": pct,
            "dir": "up" if pct > 0 else ("down" if pct < 0 else "flat"),
            "date": "", "cur": info.get("currency", "USD"),
        }
    return s


# --- OpenDART 상수 -----------------------------------------------------------
DART_BASE = "https://opendart.fss.or.kr/api"
KR_TAX_RATE = 0.22  # 법인세 실효세율 근사(ROIC NOPAT 계산용)
# 분기 보고서 코드: 최신 분기 우선 탐색(3분기 → 반기 → 1분기), 연간은 TTM 보정용
REPRT_QUARTERLY = ["11014", "11012", "11013"]
REPRT_ANNUAL = "11011"


def _to_num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _dart_statements(api_key: str, corp_code: str, year: int, reprt_code: str) -> list[dict]:
    """fnlttSinglAcntAll(전체 재무제표) 호출. 연결(CFS) 우선, 없으면 별도(OFS).

    반환: [{sj, nm, th(당기누계), fr(전년동기누계)}]. 실패 시 [].
    """
    import requests  # 지연 임포트

    for fs_div in ("CFS", "OFS"):
        try:
            resp = requests.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": api_key, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div,
                },
                timeout=20,
            )
            data = resp.json()
            if data.get("status") != "000":
                continue
            rows = [
                {
                    "sj": it.get("sj_div"),
                    "nm": (it.get("account_nm") or "").replace(" ", ""),
                    "th": _to_num(it.get("thstrm_amount")),       # 당기(분기 단독 or 연간)
                    "add": _to_num(it.get("thstrm_add_amount")),  # 당기 누계(YTD)
                    "fr": _to_num(it.get("frmtrm_amount")),       # 전기(연간 보고서에만 존재)
                }
                for it in data.get("list", [])
            ]
            if rows:
                return rows
        except Exception:  # noqa: BLE001 — 네트워크/JSON 오류는 폴백 처리
            continue
    return []


def _pick(rows: list[dict], sj: str, *keywords: str) -> Optional[dict]:
    for r in rows:
        if r["sj"] == sj and any(k in r["nm"] for k in keywords):
            return r
    return None


def _cum(row: Optional[dict]) -> Optional[float]:
    """손익/현금흐름 항목의 당기 누계(YTD). 분기 보고서는 add(누계), 연간은 th."""
    if not row:
        return None
    return row["add"] if row["add"] is not None else row["th"]


def _ttm(cur_row: Optional[dict], prior_full_row: Optional[dict],
         prior_same_q_row: Optional[dict]) -> Optional[float]:
    """TTM(최근 4개 분기 합) = 당기누계 + 전년연간 - 전년동기누계.

    fnlttSinglAcntAll 분기 응답은 전년동기(frmtrm)를 주지 않으므로
    전년 동일 분기 보고서를 별도 조회해 prior_same_q_row 로 받는다.
    세 값이 모두 있어야 신뢰 가능 → 하나라도 없으면 None(Yahoo 폴백).
    """
    cur = _cum(cur_row)
    prior_full = prior_full_row["th"] if prior_full_row else None
    prior_q = _cum(prior_same_q_row)
    if cur is None or prior_full is None or prior_q is None:
        return None
    return cur + prior_full - prior_q


# KRX 시가총액 캐시(전 종목을 1회만 받아 채운다)
_KRX_CAP: dict[str, float] = {}
_KRX_LOADED = False
_KRX_SRC = ""  # 실제 사용된 출처: "KRX-OpenAPI" | "FDR" | ""
KRX_OPENAPI_BASE = "http://data-dbg.krx.co.kr/svc/apis/sto"


def _recent_basdd(n: int = 8) -> list[str]:
    """최근 n일의 YYYYMMDD(오늘→과거). 비영업일은 KRX가 빈 데이터를 주므로 순회용."""
    today = datetime.now(KST).date()
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _norm_code(raw: str) -> str:
    """KRX ISU_CD → 6자리 단축코드. ISIN(KR7...) 형태면 단축코드 부분 추출."""
    s = (raw or "").strip()
    if len(s) >= 12 and s[:2].isalpha():  # 예: KR7005930003
        return s[3:9]
    return s


def _load_krx_openapi() -> bool:
    """공식 KRX OpenAPI(유가증권+코스닥 일별매매정보)로 시가총액 적재."""
    key = os.environ.get("KRX_API_KEY")
    if not key:
        return False
    import requests
    ok = False
    for svc in ("stk_bydd_trd", "ksq_bydd_trd"):  # KOSPI, KOSDAQ
        for d in _recent_basdd():
            try:
                r = requests.get(f"{KRX_OPENAPI_BASE}/{svc}",
                                 headers={"AUTH_KEY": key}, params={"basDd": d}, timeout=20)
                if r.status_code != 200:
                    if r.status_code in (401, 403):  # 키 미인증 → 즉시 폴백
                        print(f"  [warn] KRX OpenAPI 인증 실패({r.status_code}: "
                              f"{r.text[:60]}) → FDR 폴백", file=sys.stderr)
                        return ok
                    continue
                rows = r.json().get("OutBlock_1") or []
                if not rows:
                    continue  # 비영업일 → 이전 날짜 시도
                for it in rows:
                    code = _norm_code(it.get("ISU_CD", ""))
                    cap = _to_num(it.get("MKTCAP"))
                    if code and cap:
                        _KRX_CAP[code] = cap
                ok = True
                break  # 이 시장은 적재 완료
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] KRX OpenAPI 오류({e})", file=sys.stderr)
                return ok
    return ok


def _load_krx_fdr() -> bool:
    """FinanceDataReader(KRX 미러)로 시가총액 적재."""
    if not os.environ.get("SSL_CERT_FILE"):  # Python 프레임워크 빌드 인증서 보정
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:  # noqa: BLE001
            pass
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        col = next((c for c in ("Marcap", "MarCap", "시가총액") if c in df.columns), None)
        if not col:
            return False
        for code, cap in zip(df["Code"], df[col]):
            _KRX_CAP[str(code)] = float(cap)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] FDR 시총 로드 실패({e})", file=sys.stderr)
        return False


def _krx_market_cap(code6: str) -> Optional[float]:
    """KRX 공식 시가총액(원). 공식 OpenAPI 우선 → FDR 폴백 → (없으면 None→Yahoo)."""
    global _KRX_LOADED, _KRX_SRC
    if not _KRX_LOADED:
        _KRX_LOADED = True
        if _load_krx_openapi():
            _KRX_SRC = "KRX-OpenAPI"
        elif _load_krx_fdr():
            _KRX_SRC = "FDR"
    return _KRX_CAP.get(code6)


def fetch_dart(name: str, ticker: str, section: str, corp_code: str = "") -> Stock:
    """DART 공시 최신 분기 보고서 기반 펀더멘털 + KRX 시가총액으로 멀티플 산출.

    - 손익 항목(당기순이익·영업이익)은 TTM(최근 4분기)으로 환산
    - 재무상태 항목(자본총계·부채총계·현금)은 분기말 시점값 사용
    - 시가총액은 KRX(FinanceDataReader) 우선, 실패 시 Yahoo 폴백
    환경변수 DART_API_KEY 또는 corp_code 부재 시 Yahoo 폴백.
    """
    base = fetch_yahoo(name, ticker, section)  # 시장가 + 결측 폴백용
    api_key = os.environ.get("DART_API_KEY")
    if api_key and not corp_code:  # corp_code 미지정 시 DART 마스터에서 자동 해석
        _load_corp_map()
        corp_code = _CORP_MAP.get(ticker.split(".")[0], "")
    if not api_key or not corp_code:
        base.source = "dart(폴백:yahoo)"
        return base

    # 시가총액(원) — DART는 시세 미제공. KRX 공식값 우선, 실패 시 Yahoo 폴백.
    code6 = ticker.split(".")[0]
    mcap = _krx_market_cap(code6)
    if mcap is None:
        try:
            import yfinance as yf
            mcap = yf.Ticker(ticker).info.get("marketCap")
        except Exception:  # noqa: BLE001
            mcap = None

    # 최신 분기 보고서 탐색: 올해 → 작년, 3Q → 반기 → 1Q
    cur_year = int(datetime.now(KST).strftime("%Y"))
    rows, used_year, used_reprt = [], None, None
    for y in (cur_year, cur_year - 1):
        for rc in REPRT_QUARTERLY:
            rows = _dart_statements(api_key, corp_code, y, rc)
            if rows:
                used_year, used_reprt = y, rc
                break
        if rows:
            break
    if not rows:
        base.source = "dart(폴백:yahoo)"
        return base

    # 재무상태(시점값) — 분기말 잔액 그대로
    equity = _pick(rows, "BS", "자본총계")
    liab = _pick(rows, "BS", "부채총계")
    cash = _pick(rows, "BS", "현금및현금성자산", "현금과현금성자산")
    total_equity = equity["th"] if equity else None
    total_liab = liab["th"] if liab else None
    cash_amt = (cash["th"] if cash and cash["th"] is not None else 0.0)

    # 손익 TTM 환산: 전년 연간(11011) + 전년 동일분기 보고서로 보정
    prior_annual = _dart_statements(api_key, corp_code, used_year - 1, REPRT_ANNUAL)
    prior_q = _dart_statements(api_key, corp_code, used_year - 1, used_reprt)

    def ttm_of(*keys: str, sj_primary: str = "IS") -> Optional[float]:
        cur = _pick(rows, sj_primary, *keys) or _pick(rows, "CIS", *keys)
        pf = _pick(prior_annual, sj_primary, *keys) or _pick(prior_annual, "CIS", *keys)
        pq = _pick(prior_q, sj_primary, *keys) or _pick(prior_q, "CIS", *keys)
        return _ttm(cur, pf, pq)

    # 순이익 계정명은 보고서별로 다름: 분기/반기/당기순이익 (연간=당기순이익)
    ni_keys = ("당기순이익", "분기순이익", "반기순이익")
    ni_ttm = ttm_of(*ni_keys)
    op_ttm = ttm_of("영업이익")

    # 이익성장률(TTM vs 전년연간) → PEG용
    ni_growth = None
    prior_ni = _pick(prior_annual, "IS", *ni_keys) or _pick(prior_annual, "CIS", *ni_keys)
    if ni_ttm and prior_ni and prior_ni["th"] and prior_ni["th"] > 0:
        ni_growth = (ni_ttm / prior_ni["th"] - 1) * 100

    # --- 멀티플 산출 ---
    # PER·PBR·ROIC·PEG 는 DART 기준, EV/EBITDA 는 감가상각이 요약 재무제표에
    # 누락되는 경우가 많아 Yahoo(enterpriseToEbitda)를 사용한다.
    per = (mcap / ni_ttm) if (mcap and ni_ttm and ni_ttm > 0) else None
    pbr = (mcap / total_equity) if (mcap and total_equity and total_equity > 0) else None
    roic = None
    if op_ttm is not None and total_equity and total_liab is not None:
        invested = total_equity + total_liab - cash_amt  # 투하자본 근사
        if invested > 0:
            roic = op_ttm * (1 - KR_TAX_RATE) / invested * 100
    peg = (per / ni_growth) if (per and ni_growth and ni_growth > 0) else None

    # DART 산출값으로 덮어쓰되, 비현실적 이상치는 Yahoo 값 유지(폴백)
    def put(field: str, val: Optional[float], lo: float, hi: float) -> None:
        if val is not None and lo < val < hi:
            setattr(base, field, round(val, 3))

    put("per", per, 0, 300)
    put("pbr", pbr, 0, 30)
    put("roic", roic, -60, 90)
    put("peg", peg, 0, 5)
    # ev_ebitda 는 base(Yahoo) 값을 그대로 사용
    if mcap:
        base.market_cap = round(mcap / 1e12, 1)  # 조 원
    base.currency = "KRW"
    # 핵심 항목(자본/순이익)이 DART에서 왔으면 정식 dart 출처로 표기
    base.source = "dart" if (total_equity is not None and ni_ttm is not None) else "dart(부분:yahoo)"
    return base


def _clean(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return round(f, 3)
    except (TypeError, ValueError):
        return None


def collect_live() -> tuple[list[Stock], str]:
    from concurrent.futures import ThreadPoolExecutor
    _krx_market_cap("005930")  # KRX 시총 캐시 프리워밍(병렬 경쟁 방지)
    items = [(section, r) for section, rows in UNIVERSE.items() for r in rows]

    def fetch_one(arg: tuple) -> Optional[Stock]:
        section, r = arg
        try:
            if r["source"] == "dart":
                s = fetch_dart(r["name"], r["ticker"], section, r.get("corp_code", ""))
            else:
                s = fetch_yahoo(r["name"], r["ticker"], section)
            fetch_history(s)
            fetch_supply(s)
            fetch_quote(s)
            return s
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {r['name']}({r['ticker']}) 수집 실패: {e}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        stocks = [s for s in ex.map(fetch_one, items) if s]
    return stocks, datetime.now(KST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 저평가 필터링 / 스코어링
# ---------------------------------------------------------------------------
# 지표별 현실적 범위. 벗어나면 데이터 오류로 보고 None 처리(소스 무관).
PLAUSIBLE = {
    "per": (0, 300),
    "pbr": (0, 50),
    "ev_ebitda": (0, 100),
    "roic": (-100, 150),
    "peg": (0, 5),
}


def sanitize(stocks: list[Stock]) -> None:
    """DART/Yahoo 공통: 비현실적 지표값을 None으로 정리해 통계 왜곡을 막는다."""
    for s in stocks:
        for m, (lo, hi) in PLAUSIBLE.items():
            v = getattr(s, m)
            if v is not None and not (lo < v < hi):
                setattr(s, m, None)


def section_peer_avg(stocks: list[Stock], metric: str) -> Optional[float]:
    """동종업계 절사평균(trimmed mean).

    양수 표본만 사용하고, 표본이 5개 이상이면 최댓값·최솟값 1개씩 제거해
    이상치(예: 비정상적으로 높은 PBR)가 섹션 평균을 왜곡하지 않도록 한다.
    """
    vals = sorted(
        v for s in stocks
        if (v := s.metric(metric)) is not None and v > 0
    )
    if not vals:
        return None
    if len(vals) >= 5:
        vals = vals[1:-1]
    return sum(vals) / len(vals)


# 섹터 성격 — '하드웨어'(자산집약·순환: 발전소·변압기·메모리 fab 등 유형자산/장부가 중시 → PBR·EV/EBITDA),
# '소프트웨어'(성장·자본효율 중시 → ROIC·PEG: 냉각·광통신·연산 반도체는 수주/성장 모멘텀이 핵심).
HARDWARE_SECTORS = {"전력 인프라/장비", "에너지·원전", "SSD/메모리"}

# 섹터별 멀티플 할인 가중치(value_score): 하드웨어는 PBR·EV/EBITDA 중시
DISC_WEIGHTS = {
    "hardware": {"per": 0.25, "pbr": 0.40, "ev_ebitda": 0.35},
    "software": {"per": 0.45, "pbr": 0.10, "ev_ebitda": 0.45},
}
# 섹터별 가점 가중치: 소프트웨어는 ROIC(자본수익률)·PEG(성장성) 비중↑
BONUS_WEIGHTS = {
    "hardware": {"roic": 0.25, "peg": 0.35},
    "software": {"roic": 0.55, "peg": 0.70},
}


def sector_profile(section: str) -> str:
    return "hardware" if section in HARDWARE_SECTORS else "software"


def momentum_1y(history: Optional[dict]) -> Optional[float]:
    """최근 1년 주가 수익률(%). 월봉 13개월 비교, 자료 짧으면 전체 구간."""
    if not history:
        return None
    c = history.get("closes") or []
    if len(c) >= 13 and c[-13]:
        return round((c[-1] / c[-13] - 1) * 100, 1)
    if len(c) >= 2 and c[0]:
        return round((c[-1] / c[0] - 1) * 100, 1)
    return None


def score(stocks: list[Stock]) -> None:
    """투자 해석 가이드를 반영한 섹터 가중 스코어링.

    - value_score: 동종평균 대비 멀티플 할인을 섹터별 가중치로 합산
      (하드웨어=PBR·EV/EBITDA 중시, 소프트웨어=PER·이익효율 중시)
    - 가점: ROIC·PEG에 섹터별 가중치 적용(소프트웨어에서 비중↑)
    - 안전마진(margin of safety): PBR<1 또는 EV/EBITDA<동종평균에 가점
    """
    by_section: dict[str, list[Stock]] = {}
    for s in stocks:
        by_section.setdefault(s.section, []).append(s)

    for section, group in by_section.items():
        peer = {m: section_peer_avg(group, m) for m in VALUATION_METRICS}
        prof = sector_profile(section)
        dw, bw = DISC_WEIGHTS[prof], BONUS_WEIGHTS[prof]
        for s in group:
            num = den = 0.0
            cheaper_count = 0
            for m in VALUATION_METRICS:
                v, avg = s.metric(m), peer[m]
                if v is None or avg is None or v <= 0:
                    continue
                disc = (avg - v) / avg  # +면 평균보다 쌈
                num += dw[m] * disc
                den += dw[m]
                if disc > 0:
                    cheaper_count += 1
                    s.cheap_flags.append(METRIC_LABEL[m])
            s.is_candidate = cheaper_count >= 2
            s.value_score = (num / den) if den else -1.0

            if s.roic is not None:
                s.quality_bonus = max(0.0, min(s.roic, 40.0)) / 40.0
            if s.peg is not None and 0 < s.peg < 1:
                s.growth_bonus = 1 - s.peg

            # 안전마진
            safety = 0.0
            if s.pbr is not None and s.pbr < 1.0:
                safety += 0.30
                s.safety_flags.append("PBR<1")
            if s.ev_ebitda is not None and peer["ev_ebitda"] is not None \
                    and s.ev_ebitda < peer["ev_ebitda"]:
                safety += 0.10
                s.safety_flags.append("EV/EBITDA<동종평균")
            s.safety_bonus = safety

            s.composite = (
                s.value_score
                + bw["roic"] * s.quality_bonus
                + bw["peg"] * s.growth_bonus
                + safety
            )

            # 추정치(선행) 신호 — 이익전망(추정PER<현재PER)·목표가 상승여력·투자의견
            fwd = []
            if s.per and s.per > 0 and s.forward_pe and s.forward_pe > 0:
                fwd.append(max(-0.5, min(0.5, (s.per - s.forward_pe) / s.per)))
            px = (s.last_close or {}).get("price")
            if s.target_price and px:
                fwd.append(max(-0.5, min(0.5, s.target_price / px - 1)))
            if s.recomm_mean:
                fwd.append(max(-0.5, min(0.5, (3 - s.recomm_mean) / 2)))
            if fwd:
                s.forward_score = round(sum(fwd) / len(fwd), 3)
                s.composite += 0.35 * s.forward_score

            # 주가 추세(모멘텀) 반영 — 하락 추세(가치 함정)에 비대칭 페널티
            s.momentum = momentum_1y(s.history)
            if s.momentum is not None:
                f = s.momentum / 100.0
                s.composite += (max(-0.5, f) * 1.2) if f < 0 else (min(0.15, f) * 0.8)
                if s.momentum < -15:
                    s.trend_warn = True


def pick_top(stocks: list[Stock], n: int = 10) -> list[Stock]:
    """전체 종합점수 순 Top N(후보군 우선, 부족 시 점수순 보충)."""
    cands = sorted([s for s in stocks if s.is_candidate],
                   key=lambda s: s.composite, reverse=True)
    if len(cands) >= n:
        return cands[:n]
    rest = sorted([s for s in stocks if s not in cands],
                  key=lambda s: s.composite, reverse=True)
    return (cands + rest)[:n]


def _quote_fields(s: Stock) -> dict:
    mom = momentum_1y(s.history)
    return {
        "marketCap": s.market_cap, "volume": s.volume,
        "week52High": s.week52_high, "week52Low": s.week52_low,
        "target": s.target_price, "recommMean": s.recomm_mean,
        "history": s.history, "lastClose": s.last_close,
        "per": s.per, "pbr": s.pbr, "forwardPE": s.forward_pe,
        "momentum": mom, "trendWarn": (mom is not None and mom < -15),
    }


def _snapshot(top: list[Stock], as_of: str) -> dict:
    snap = []
    for i, s in enumerate(top, 1):
        lc = s.last_close or {}
        snap.append({"name": s.name, "ticker": s.ticker, "rank": i,
                     "close": lc.get("price"), "cur": lc.get("cur", "USD")})
    return {"date": as_of, "top10": snap}


def bootstrap_history(stocks: list[Stock], as_of: str) -> dict:
    """기존 리포트(top3)로 과거 순위 스냅샷 복원(최초 1회).

    종가는 월봉 기준이라 같은 달은 의미가 없어 None 처리(성과추적은 실측 누적분만).
    """
    import re
    by_name = {s.name: s for s in stocks}
    cur_ym = as_of[:7]
    snaps = []
    for p in sorted(POSTS_DIR.glob("*-daily-ai-top3.md")):
        txt = p.read_text(encoding="utf-8")
        md = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", txt, re.M)
        mt = re.search(r"^top3:\s*\[(.*?)\]", txt, re.M) or re.search(r"^tags:\s*\[(.*?)\]", txt, re.M)
        if not md or not mt:
            continue
        date = md.group(1)
        if date >= as_of:
            continue
        names = [t.strip().strip('"') for t in mt.group(1).split(",")][:3]
        snap = []
        for i, n in enumerate(names, 1):
            s = by_name.get(n)
            close, cur = None, "USD"
            if s and s.history and date[:7] != cur_ym:  # 같은 달이면 종가 None
                cur = (s.last_close or {}).get("cur", "USD")
                ds = s.history["dates"]
                if date[:7] in ds:
                    close = s.history["closes"][ds.index(date[:7])]
            snap.append({"name": n, "ticker": s.ticker if s else "", "rank": i,
                         "close": close, "cur": cur})
        snaps.append({"date": date, "top10": snap})
    return {"snapshots": snaps}


def update_history(top: list[Stock], stocks: list[Stock], as_of: str) -> dict:
    """history.json에 오늘 스냅샷 추가(없으면 부트스트랩). 최근 90일 유지."""
    try:
        hist = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        hist = None
    if not hist or not hist.get("snapshots"):
        hist = bootstrap_history(stocks, as_of)
    snaps = [s for s in hist["snapshots"] if s["date"] != as_of]
    snaps.append(_snapshot(top, as_of))
    snaps.sort(key=lambda x: x["date"])
    hist = {"snapshots": snaps[-90:]}
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    return hist


def rank_deltas(hist: dict) -> dict:
    """오늘 Top 10의 전일 대비 순위 변동(+오름/−내림/None=신규)."""
    snaps = hist["snapshots"]
    if len(snaps) < 2:
        return {}
    prev = {x["name"]: x["rank"] for x in snaps[-2]["top10"]}
    return {x["name"]: (prev[x["name"]] - x["rank"]) if x["name"] in prev else None
            for x in snaps[-1]["top10"]}


def performance(hist: dict, stocks: list[Stock]) -> list[dict]:
    """과거 각 스냅샷 Top 10의 현재까지 평균 수익률."""
    cur = {s.name: (s.last_close or {}).get("price") for s in stocks}
    out = []
    for snap in hist["snapshots"][:-1]:
        rets = [cur[x["name"]] / x["close"] - 1
                for x in snap["top10"]
                if x.get("close") and cur.get(x["name"])]
        if rets:
            out.append({"date": snap["date"], "avg": round(sum(rets) / len(rets) * 100, 1),
                        "n": len(rets)})
    return out


def build_dashboard(stocks: list[Stock], top: list[Stock], as_of: str,
                    news: Optional[dict], extra: Optional[list[Stock]] = None,
                    deltas: Optional[dict] = None, perf: Optional[list] = None) -> dict:
    """메인 대시보드(검색·차트·추천여부·뉴스)용 JSON 데이터.

    stocks = AI 4개 섹션(정밀 분석), extra = KOSPI 시총상위(기본 시세).
    중복(국내 AI 종목)은 AI 풀세트를 우선한다. top = 전체 점수순 Top 10.
    """
    ranks = {t.name: i + 1 for i, t in enumerate(top)}
    # 섹터 내 순위(종합점수 내림차순) — 전체/섹터 상세 페이지 정렬용
    sec_rank: dict[str, int] = {}
    _by_sec: dict[str, list[Stock]] = {}
    for s in stocks:
        _by_sec.setdefault(s.section, []).append(s)
    for members in _by_sec.values():
        for i, s in enumerate(sorted(members, key=lambda x: x.composite, reverse=True), 1):
            sec_rank[s.name] = i
    out: dict[str, dict] = {}
    seen_tickers: set[str] = set()
    for s in stocks:
        rank = ranks.get(s.name)
        if rank:
            level, verdict = "buy", f"추천 (Top {rank})"
            reasons = [f"전체 종합점수 Top {rank}"]
        elif s.is_candidate:
            level, verdict = "watch", "관심(후보군)"
            reasons = ["동종평균 대비 멀티플이 낮은 1차 후보군"]
        else:
            level, verdict = "neutral", "중립"
            reasons = ["동종평균 대비 가격 매력이 제한적"]
        if s.safety_flags:
            reasons.append("안전마진: " + ", ".join(s.safety_flags))
        prof = sector_profile(s.section)
        out[s.name] = {
            "ticker": s.ticker, "section": s.section, "profile": prof, "ai": True,
            "lens": "유형자산(PBR·EV/EBITDA) 중심" if prof == "hardware"
                    else "자본효율·성장성(ROIC·PEG) 중심",
            "level": level, "verdict": verdict, "rank": rank,
            "composite": round(s.composite, 3), "sectorRank": sec_rank.get(s.name),
            "ev_ebitda": s.ev_ebitda, "roic": s.roic, "peg": s.peg,
            "reasons": reasons, "safety": s.safety_flags, "supply": s.supply,
            "incomeTrend": s.income_trend,
            **_quote_fields(s),
        }
        seen_tickers.add(s.ticker)

    # 업종별 시총 가중평균 대비 저평가 평가 (적자·소형주 제외)
    import statistics
    from collections import defaultdict
    extra = extra or []
    MIN_CAP = 0.1  # 조 (= 1,000억) 미만 소형주는 평균 계산에서 제외

    # 폴백 기준 = 대형주(시총 1000억+) 중앙값 (소형주 왜곡 제거)
    _p = [s.per for s in extra if s.per and s.per > 0 and s.market_cap and s.market_cap >= MIN_CAP]
    _b = [s.pbr for s in extra if s.pbr and s.pbr > 0 and s.market_cap and s.market_cap >= MIN_CAP]
    med_per = statistics.median(_p) if _p else None
    med_pbr = statistics.median(_b) if _b else None

    groups: dict[str, list[Stock]] = defaultdict(list)
    for s in extra:
        if s.industry:
            groups[s.industry].append(s)

    def wavg(members: list[Stock], metric: str) -> tuple[Optional[float], int]:
        num = den = 0.0
        n = 0
        for s in members:
            v, cap = getattr(s, metric), s.market_cap
            if v and v > 0 and cap and cap >= MIN_CAP:
                num += v * cap
                den += cap
                n += 1
        return (num / den if den else None, n)

    ind_avg: dict[str, dict] = {}
    for ind, members in groups.items():
        pa, npa = wavg(members, "per")
        pb, npb = wavg(members, "pbr")
        ind_avg[ind] = {"per": pa, "pbr": pb, "n": min(npa, npb)}

    for s in extra:
        if s.ticker in seen_tickers or s.name in out:
            continue
        avg = ind_avg.get(s.industry or "")
        use_ind = bool(avg and avg["n"] >= 3 and avg["per"] and avg["pbr"])
        if use_ind:
            b_per, b_pbr, basis = avg["per"], avg["pbr"], f"{s.industry} 업종(시총 가중) 평균"
        else:
            b_per, b_pbr, basis = med_per, med_pbr, "대형주 중앙값(업종 표본 부족)"
        vs = []
        if s.per and s.per > 0 and b_per:
            vs.append((b_per - s.per) / b_per)
        if s.pbr and s.pbr > 0 and b_pbr:
            vs.append((b_pbr - s.pbr) / b_pbr)
        score = sum(vs) / len(vs) if vs else None
        safety = s.pbr is not None and s.pbr < 1.0
        if score is None:
            level, verdict = "out", "데이터 부족"
            reasons = ["PER·PBR 데이터가 없어 평가를 산출하지 못했습니다."]
        else:
            if score > 0.20 or (safety and score > 0):
                level, verdict = "value", "저평가"
            elif score < -0.20:
                level, verdict = "rich", "고평가"
            else:
                level, verdict = "neutral", "적정"
            cmp = "낮음" if score > 0.05 else ("높음" if score < -0.05 else "유사")
            reasons = [f"{basis} 대비 PER·PBR {cmp}"]
            if safety:
                reasons.append("PBR 1배 미만(순자산 대비 저평가)")
        out[s.name] = {
            "ticker": s.ticker, "section": s.industry or "KOSPI", "profile": None, "ai": False,
            "lens": "", "level": level, "verdict": verdict, "rank": None,
            "ev_ebitda": None, "roic": None, "peg": None,
            "reasons": reasons, "safety": ["PBR<1"] if safety else [], "supply": None,
            "industry": s.industry, "basis": basis,
            "benchPER": round(b_per, 2) if b_per else None,
            "benchPBR": round(b_pbr, 2) if b_pbr else None,
            **_quote_fields(s),
        }
        seen_tickers.add(s.ticker)

    # 섹터 구조(전체 페이지 아코디언 + 섹터 상세 페이지용) — SECTION_ORDER 순서
    sectors = []
    for key in SECTION_ORDER:
        members = _by_sec.get(key, [])
        names = [s.name for s in sorted(members, key=lambda x: sec_rank.get(x.name, 99))]
        meta = SECTOR_META.get(key, {})
        sectors.append({"key": key, "emoji": meta.get("emoji", ""),
                        "slug": meta.get("slug", ""), "desc": meta.get("desc", ""),
                        "profile": sector_profile(key), "members": names, "count": len(names)})

    return {"as_of": as_of, "top3": [t.name for t in top[:3]], "top10": [t.name for t in top],
            "rankDeltas": deltas or {}, "performance": perf or [],
            "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
            "stocks": out, "news": news or {}, "sectors": sectors,
            "count": len(out), "industries": len(ind_avg),
            "benchmark": {"medPER": round(med_per, 2) if med_per else None,
                          "medPBR": round(med_pbr, 2) if med_pbr else None}}


# ---------------------------------------------------------------------------
# 리포트 생성
# ---------------------------------------------------------------------------
def reason_lines(s: Stock, peer_avg: dict[str, Optional[float]]) -> list[str]:
    """Top3 종목 3줄 분석."""
    lines = []
    # 1줄: 동종평균 대비 멀티플 할인
    cheap_desc = []
    for m in VALUATION_METRICS:
        v, avg = s.metric(m), peer_avg.get(m)
        if v is not None and avg is not None and v > 0 and v < avg:
            cheap_desc.append(f"{METRIC_LABEL[m]} {v:g}배(섹션평균 {avg:.1f}배)")
    if cheap_desc:
        lines.append(f"섹션 동종평균을 밑도는 멀티플: {', '.join(cheap_desc[:2])} — 가치 대비 가격 매력.")
    else:
        lines.append("주요 멀티플이 섹션 평균 수준으로, 펀더멘털 대비 과열되지 않음.")
    # 2줄: ROIC 수익성
    if s.roic is not None:
        q = "우수한" if s.roic >= 15 else "안정적인"
        lines.append(f"ROIC {s.roic:g}% — {q} 자본 수익성으로 저평가가 '저성장 함정'이 아님을 시사.")
    # 3줄: PEG 성장성
    if s.peg is not None and s.peg > 0:
        if s.peg < 1:
            lines.append(f"PEG {s.peg:g}(<1) — 이익성장률 대비 주가가 싸 성장 프리미엄 미반영 구간.")
        else:
            lines.append(f"PEG {s.peg:g} — 성장 기대가 일부 반영됐으나 동종 대비 멀티플 매력 유지.")
    while len(lines) < 3:
        lines.append("최신 분기 실적 기준 펀더멘털 대비 가격 매력 유지.")
    return lines[:3]


def fmt(v: Optional[float], suffix: str = "") -> str:
    return f"{v:g}{suffix}" if v is not None else "—"


def build_markdown(stocks: list[Stock], top: list[Stock], as_of: str, mode: str,
                   news: Optional[dict] = None, deltas: Optional[dict] = None,
                   perf: Optional[list] = None) -> str:
    top3 = top[:3]
    deltas = deltas or {}

    def delta_str(name: str) -> str:
        if name not in deltas:
            return "—"
        d = deltas[name]
        if d is None:
            return "NEW"
        return "—" if d == 0 else (f"▲{d}" if d > 0 else f"▼{-d}")
    by_section: dict[str, list[Stock]] = {}
    for s in stocks:
        by_section.setdefault(s.section, []).append(s)
    peer_by_section = {
        sec: {m: section_peer_avg(g, m) for m in VALUATION_METRICS}
        for sec, g in by_section.items()
    }
    out: list[str] = []

    # --- Front matter (Hugo / YAML) ---
    tags = [t.name for t in top3] + ["저평가", "밸류에이션"]
    out.append("---")
    out.append(f'title: "[{as_of}] 오늘의 AI 밸류체인 저평가 Top 10"')
    out.append(f"date: {as_of}T07:00:00+09:00")
    out.append("draft: false")
    out.append("categories: [\"AI밸류체인투자\", \"데일리리포트\"]")
    out.append(f"tags: [{', '.join(json.dumps(t, ensure_ascii=False) for t in tags)}]")
    out.append(f"top3: [{', '.join(json.dumps(t.name, ensure_ascii=False) for t in top3)}]")
    out.append("---")
    out.append("")

    # --- 면책 문구(최상단) ---
    out.append("> **면책 고지** — 본 리포트는 재무제표 기반의 정량적 분석 자료이며, "
               "실제 투자 결과에 대한 책임은 투자자 본인에게 있습니다.")
    out.append("")
    out.append(f"데이터 기준일: {as_of} · 국내 종목은 DART 공시 최신 분기 보고서와 KRX 시가총액, "
               f"해외 종목은 Yahoo Finance · 생성 모드 `{mode}`")
    out.append("")
    # 용어 쉽게 보기 — 클릭하면 모달
    out.append('<p class="glossary-chips">용어 설명(클릭 시 쉬운 풀이): '
               + " ".join(f'<button class="term" data-term="{k}">{lbl}</button>'
                          for k, lbl in [("per", "PER"), ("pbr", "PBR"),
                                         ("ev_ebitda", "EV/EBITDA"), ("roic", "ROIC"),
                                         ("peg", "PEG"), ("mktcap", "시가총액"),
                                         ("supply", "수급")])
               + "</p>")
    out.append("")

    # --- 오늘의 AI 밸류체인 저평가 Top 10 (전체 종합점수 순) ---
    out.append("## 오늘의 AI 밸류체인 저평가 Top 10")
    out.append("")
    out.append("| 섹션 | 종목 | 전일대비 | 종합점수 | PER | PBR | EV/EBITDA | ROIC | PEG | 1년주가 | 비고 |")
    out.append("|:---|:---|:---:|---:|---:|---:|---:|---:|---:|---:|:---|")
    for i, s in enumerate(top, 1):
        mom = f"{s.momentum:+g}%" if s.momentum is not None else "—"
        note = "[하락주의]" if s.trend_warn else ""
        out.append(
            f"| {s.section} | **{i}. {s.name}** ({s.ticker}) | {delta_str(s.name)} | {s.composite:.3f} | "
            f"{fmt(s.per)} | {fmt(s.pbr)} | {fmt(s.ev_ebitda)} | "
            f"{fmt(s.roic, '%')} | {fmt(s.peg)} | {mom} | {note} |"
        )
    out.append("")
    out.append("<sub>전체 종합점수 순. 섹션 편중 없이 점수 그대로 — 본인 판단으로 선택하세요. "
               "`전일대비`는 어제 순위 대비 변동(▲상승/▼하락/NEW 신규), "
               "`[하락주의]`는 최근 1년 주가 하락 추세(가치 함정 가능)입니다.</sub>")
    out.append("")

    # --- 성과 추적 (과거 Top 10의 현재까지 수익률) ---
    if perf:
        out.append("### 성과 추적")
        out.append("")
        out.append("> 과거 추천 Top 10이 그 후 실제로 어떻게 됐는지(현재가 대비 평균 수익률). "
                   "시스템의 실효성을 스스로 검증합니다.")
        out.append("")
        out.append("| 추천일 | 경과 | Top 10 평균 수익률 |")
        out.append("|:---|:---|---:|")
        from datetime import date as _date
        y, m, d = (int(x) for x in as_of.split("-"))
        for row in perf[-7:][::-1]:
            try:
                py, pm, pd = (int(x) for x in row["date"].split("-"))
                days = (_date(y, m, d) - _date(py, pm, pd)).days
                elapsed = f"{days}일 전"
            except Exception:  # noqa: BLE001
                elapsed = "—"
            sign = "+" if row["avg"] >= 0 else ""
            out.append(f"| {row['date']} | {elapsed} | **{sign}{row['avg']}%** |")
        out.append("")

    # --- 상위 3종목 선정 근거 ---
    out.append("### 상위 3종목 선정 근거")
    out.append("")
    for i, s in enumerate(top3, 1):
        lens = "유형자산(PBR·EV/EBITDA) 중심" if sector_profile(s.section) == "hardware" \
            else "자본효율·성장성(ROIC·PEG) 중심"
        out.append(f"**{i}. {s.name} ({s.ticker})** · {s.section} — {lens}")
        out.append("")
        for line in reason_lines(s, peer_by_section[s.section]):
            out.append(f"- {line}")
        if s.safety_flags:
            out.append(f"- 안전마진: {', '.join(s.safety_flags)} — 가격 하방을 받쳐주는 신호.")
        if s.trend_warn:
            out.append(f"- **[주의] 최근 1년 주가 {s.momentum:+g}% 하락 추세** — 저평가가 "
                       "가치 함정(falling knife)일 수 있어 추세·실적 확인 필요.")
        out.append("")

    # --- 투자 관점: 데이터 해석법 ---
    out.append("## 투자 관점: 데이터 해석법")
    out.append("")
    out.append("Top 10은 아래 기준의 전체 종합점수 순입니다. 무조건 오르는 종목은 없으며, "
               "재무적으로 우량하면서 시장의 주목은 아직 낮은 종목을 찾는 데 최적화되어 있습니다. "
               "최종 선택은 투자자 본인의 판단입니다.")
    out.append("")
    out.append("- **안전마진** — PBR이 1배 미만이거나 EV/EBITDA가 동종 업계 평균보다 낮으면, "
               "시장이 그 기업의 AI 잠재력을 아직 가격에 반영하지 않았다는 신호로 보고 가점합니다.")
    out.append("- **섹터별 지표 가중치** — 섹터 성격에 따라 보는 지표의 비중을 달리합니다.")
    out.append("")
    out.append("| 섹터 | 우선 지표 | 이유 |")
    out.append("|:---|:---|:---|")
    out.append("| 하드웨어(반도체·인프라·데이터센터) | PBR, EV/EBITDA | 공장·장비 등 유형자산 가치가 크다 |")
    out.append("| 소프트웨어·AI 모델 | ROIC, PEG | 무형자산이 핵심이라 자본효율·성장성이 중요하다 |")
    out.append("")
    out.append("- **활용법** — 매일의 Top 3를 별도 시트에 기록해 두면, 일정 기간 뒤 "
               "어떤 종목이 먼저 상승 탄력을 받는지 패턴을 확인할 수 있습니다.")
    out.append("")

    # --- 섹션별 시장 동향 ---
    out.append("## 섹션별 시장 동향")
    out.append("")
    for sec in SECTION_ORDER:
        group = by_section.get(sec, [])
        if not group:
            continue
        peer = peer_by_section[sec]
        # 섹션 내 저평가 순 정렬
        group_sorted = sorted(group, key=lambda s: s.composite, reverse=True)
        cheapest = group_sorted[0]
        priciest = min(
            (s for s in group if s.per is not None and s.per > 0),
            key=lambda s: -s.per, default=None,
        )
        out.append(f"### {sec}")
        def avg1(m: str) -> str:
            return f"{peer[m]:.1f}" if peer.get(m) is not None else "—"
        summary = (
            f"동종 {len(group)}개 종목 기준 평균 PER {avg1('per')}배 · "
            f"PBR {avg1('pbr')}배 · EV/EBITDA {avg1('ev_ebitda')}배. "
            f"**{cheapest.name}**이(가) 동종평균 대비 가장 저평가"
        )
        if priciest is not None:
            summary += f"되어 있으며, **{priciest.name}**은(는) 고밸류 구간."
        else:
            summary += "되어 있습니다."
        out.append(summary)
        out.append("")
        out.append("| 종목 | PER | PBR | EV/EBITDA | ROIC | PEG | 후보 | 가격 매력 |")
        out.append("|:---|---:|---:|---:|---:|---:|:---:|:---|")
        for s in group_sorted:
            cand = "O" if s.is_candidate else "—"
            flags = ", ".join(sorted(set(s.cheap_flags))) if s.cheap_flags else "—"
            out.append(
                f"| {s.name} ({s.ticker}) | {fmt(s.per)} | {fmt(s.pbr)} | "
                f"{fmt(s.ev_ebitda)} | {fmt(s.roic, '%')} | {fmt(s.peg)} | {cand} | {flags} |"
            )
        out.append("")

    # --- 섹션별 주요 뉴스 ---
    if news:
        out.append("## 섹션별 주요 뉴스 Top 3")
        out.append("")
        for sec in SECTION_ORDER:
            items = news.get(sec) or []
            if not items:
                continue
            out.append(f"### {sec}")
            for it in items[:3]:
                title = it.get("title", "").replace("|", "ǀ")
                link = it.get("link", "")
                meta = " · ".join(x for x in (it.get("source", ""), it.get("date", "")) if x)
                head = f"[{title}]({link})" if link and link != "#" else title
                out.append(f"1. {head}" + (f" — {meta}" if meta else ""))
            out.append("")

    # --- 방법론 ---
    out.append("<details><summary>분석 방법론</summary>")
    out.append("")
    out.append("1. 4개 섹션 전 종목의 PER·PBR·EV/EBITDA·ROIC·PEG를 수집·계산한다.")
    out.append("2. 섹션 내 동종업계 평균(이상치 보정 절사평균) 대비 멀티플이 낮은 종목을 "
               "1차 후보군으로 선정한다(과반 지표가 평균 미만이면 후보 O).")
    out.append("3. 섹터 성격에 따라 가중치를 달리한다 — 하드웨어는 PBR·EV/EBITDA, "
               "소프트웨어·AI는 ROIC·PEG의 비중을 높인다.")
    out.append("4. PBR 1배 미만 또는 EV/EBITDA가 동종평균 미만이면 안전마진 가점을 더한다.")
    out.append("5. **주가 추세(모멘텀)** — 최근 1년 수익률이 하락 추세면 종합점수에 비대칭 페널티를 주고, "
               "−15% 이하면 '가치 함정 주의'로 표시한다(저평가가 단지 주가 급락 때문일 위험 차단).")
    out.append("6. `종합점수 = 섹터가중 멀티플 할인 + ROIC·PEG 가점 + 안전마진 + 추세조정` "
               "상위 3종목을 Top 3로 선정한다.")
    out.append("")
    out.append("국내 종목은 DART 공시 최신 분기 보고서(펀더멘털)와 KRX 시가총액을 적용하며, "
               "해외 종목은 Yahoo Finance를 사용한다.")
    out.append("</details>")
    out.append("")

    # --- 차트 데이터(레이아웃의 차트 위젯이 읽음) ---
    chart_payload = {
        s.name: {"ticker": s.ticker, "section": s.section,
                 "dates": s.history["dates"], "closes": s.history["closes"],
                 "supply": s.supply, "lastClose": s.last_close}
        for s in stocks if s.history
    }
    out.append('<script id="chart-data" type="application/json">')
    out.append(json.dumps(chart_payload, ensure_ascii=False))
    out.append("</script>")
    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="AI 섹터 저평가 종목 탐색기")
    ap.add_argument("--mode", choices=["sample", "live"], default="live")
    ap.add_argument("--date", help="리포트 기준일(YYYY-MM-DD). 미지정 시 KST 오늘.")
    ap.add_argument("--out", help="출력 경로. 미지정 시 _posts/<date>-daily-ai-top3.md")
    ap.add_argument("--stdout", action="store_true", help="파일 대신 표준출력으로 인쇄")
    args = ap.parse_args()

    if args.mode == "sample":
        stocks, as_of = load_sample()
    else:
        try:
            stocks, as_of = collect_live()
        except Exception as e:  # noqa: BLE001
            print(f"[error] live 수집 실패({e}). sample 모드로 폴백합니다.", file=sys.stderr)
            stocks, as_of = load_sample()

    if args.date:
        as_of = args.date

    sanitize(stocks)
    score(stocks)
    top = pick_top(stocks, 10)

    if args.mode == "sample":
        news = {sec: synth_news(sec) for sec in SECTION_ORDER}
        kospi: list[Stock] = []
    else:
        news = {sec: fetch_news(q) for sec, q in SECTION_NEWS_QUERY.items()}
        print("[info] KOSPI 전 종목 수집 중...", file=sys.stderr)
        kospi = collect_kospi()  # 전 종목

    # 일별 Top 10 스냅샷 누적 → 순위 변동 + 성과 추적
    hist = update_history(top, stocks, as_of)
    deltas = rank_deltas(hist)
    perf = performance(hist, stocks)

    md = build_markdown(stocks, top, as_of, args.mode, news, deltas, perf)

    # 메인 대시보드 데이터(AI 정밀 + KOSPI 전 종목 검색)
    dash = build_dashboard(stocks, top, as_of, news, kospi, deltas, perf)
    if args.mode != "sample":
        print("[info] 저평가/AI 국내 종목 3년 순이익 추세 수집 중...", file=sys.stderr)
        enrich_income_trends(dash)
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(json.dumps(dash, ensure_ascii=False), encoding="utf-8")

    if args.stdout:
        print(md)
        return 0

    out_path = Path(args.out) if args.out else POSTS_DIR / f"{as_of}-daily-ai-top3.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ 리포트 생성: {out_path}")
    print(f"   Top 10: {', '.join(f'{s.name}({s.composite:.3f})' for s in top)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
