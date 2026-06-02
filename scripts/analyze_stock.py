#!/usr/bin/env python3
"""AI 섹터 저평가 종목 탐색기 — 핵심 분석 로직.

4개 섹션(인프라/반도체, 클라우드/데이터센터, 모델/소프트웨어, AI 애플리케이션)
전 종목의 [PER, PBR, EV/EBITDA, ROIC, PEG]를 수집/계산하고,
섹션 내 동종업계 평균 대비 저평가 종목을 선별해
'오늘의 AI 저평가 Top 3' Jekyll 포스트를 생성한다.

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
KST = timezone(timedelta(hours=9))

SECTION_ORDER = [
    "인프라/반도체",
    "클라우드/데이터센터",
    "모델/소프트웨어",
    "AI 애플리케이션",
]

# 분석 유니버스(섹션별 종목). live 모드의 수집 대상이기도 하다.
UNIVERSE: dict[str, list[dict]] = {
    "인프라/반도체": [
        {"name": "엔비디아", "ticker": "NVDA", "source": "yahoo"},
        {"name": "AMD", "ticker": "AMD", "source": "yahoo"},
        {"name": "TSMC", "ticker": "TSM", "source": "yahoo"},
        {"name": "삼성전자", "ticker": "005930.KS", "source": "dart", "corp_code": "00126380"},
        {"name": "SK하이닉스", "ticker": "000660.KS", "source": "dart", "corp_code": "00164779"},
        {"name": "한미반도체", "ticker": "042700.KS", "source": "dart", "corp_code": "00161383"},
        {"name": "브로드컴", "ticker": "AVGO", "source": "yahoo"},
        {"name": "마이크론", "ticker": "MU", "source": "yahoo"},
        {"name": "ASML", "ticker": "ASML", "source": "yahoo"},
    ],
    "클라우드/데이터센터": [
        {"name": "마이크로소프트", "ticker": "MSFT", "source": "yahoo"},
        {"name": "아마존", "ticker": "AMZN", "source": "yahoo"},
        {"name": "알파벳", "ticker": "GOOGL", "source": "yahoo"},
        {"name": "오라클", "ticker": "ORCL", "source": "yahoo"},
        {"name": "이퀴닉스", "ticker": "EQIX", "source": "yahoo"},
        {"name": "네이버", "ticker": "035420.KS", "source": "dart", "corp_code": "00266961"},
        {"name": "코어위브", "ticker": "CRWV", "source": "yahoo"},
    ],
    "모델/소프트웨어": [
        {"name": "메타", "ticker": "META", "source": "yahoo"},
        {"name": "팔란티어", "ticker": "PLTR", "source": "yahoo"},
        {"name": "C3.ai", "ticker": "AI", "source": "yahoo"},
        {"name": "카카오", "ticker": "035720.KS", "source": "dart", "corp_code": "00258801"},
        {"name": "스노우플레이크", "ticker": "SNOW", "source": "yahoo"},
        {"name": "SAP", "ticker": "SAP", "source": "yahoo"},
        {"name": "IBM", "ticker": "IBM", "source": "yahoo"},
    ],
    "AI 애플리케이션": [
        {"name": "어도비", "ticker": "ADBE", "source": "yahoo"},
        {"name": "세일즈포스", "ticker": "CRM", "source": "yahoo"},
        {"name": "서비스나우", "ticker": "NOW", "source": "yahoo"},
        {"name": "듀오링고", "ticker": "DUOL", "source": "yahoo"},
        {"name": "더존비즈온", "ticker": "012510.KS", "source": "dart", "corp_code": "00172291"},
        {"name": "테슬라", "ticker": "TSLA", "source": "yahoo"},
        {"name": "크라우드스트라이크", "ticker": "CRWD", "source": "yahoo"},
        {"name": "인튜이티브서지컬", "ticker": "ISRG", "source": "yahoo"},
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
    composite: float = 0.0
    is_candidate: bool = False     # 1차 후보군 여부
    cheap_flags: list[str] = field(default_factory=list)
    history: Optional[dict] = None  # {"dates": [...], "closes": [...]} 최근 3년 월봉
    supply: Optional[dict] = None   # 수급: {dates, foreign, organ, individual, foreignHold} 최근 10일
    last_close: Optional[dict] = None  # 전일 종가: {price, pct, dir, date, cur}

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


SECTION_NEWS_QUERY = {
    "인프라/반도체": "AI 반도체 OR HBM OR 엔비디아 OR 파운드리",
    "클라우드/데이터센터": "AI 클라우드 OR 데이터센터 OR AWS OR Azure",
    "모델/소프트웨어": "AI 모델 OR LLM OR 생성형 AI OR 오픈AI",
    "AI 애플리케이션": "AI 에이전트 OR AI 서비스 OR AI 애플리케이션",
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
    stocks: list[Stock] = []
    for section, rows in UNIVERSE.items():
        for r in rows:
            try:
                if r["source"] == "dart":
                    s = fetch_dart(r["name"], r["ticker"], section, r.get("corp_code", ""))
                else:
                    s = fetch_yahoo(r["name"], r["ticker"], section)
                fetch_history(s)  # 최근 3년 월봉
                fetch_supply(s)   # 최근 10일 투자자별 순매수(국내)
                stocks.append(s)
            except Exception as e:  # noqa: BLE001 — 종목 1건 실패가 전체를 막지 않도록
                print(f"  [warn] {r['name']}({r['ticker']}) 수집 실패: {e}", file=sys.stderr)
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


# 섹터 성격 — 하드웨어는 유형자산(공장·장비·데이터센터) 가치가 크고,
# 소프트웨어/AI 모델은 무형자산이 핵심이라 자본효율·성장성이 더 중요하다.
HARDWARE_SECTORS = {"인프라/반도체", "클라우드/데이터센터"}

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


def pick_top3(stocks: list[Stock]) -> list[Stock]:
    cands = [s for s in stocks if s.is_candidate]
    cands.sort(key=lambda s: s.composite, reverse=True)
    return cands[:3]


def build_dashboard(stocks: list[Stock], top3: list[Stock], as_of: str,
                    news: Optional[dict]) -> dict:
    """메인 대시보드(검색·차트·추천여부·뉴스)용 JSON 데이터."""
    ranks = {t.name: i + 1 for i, t in enumerate(top3)}
    out: dict[str, dict] = {}
    for s in stocks:
        rank = ranks.get(s.name)
        if rank:
            level, verdict = "buy", "추천"
            reasons = [f"섹터 가중 종합점수 상위 — 오늘의 Top {rank}"]
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
            "ticker": s.ticker, "section": s.section, "profile": prof,
            "lens": "유형자산(PBR·EV/EBITDA) 중심" if prof == "hardware"
                    else "자본효율·성장성(ROIC·PEG) 중심",
            "level": level, "verdict": verdict, "rank": rank,
            "per": s.per, "pbr": s.pbr, "ev_ebitda": s.ev_ebitda,
            "roic": s.roic, "peg": s.peg,
            "reasons": reasons, "safety": s.safety_flags,
            "history": s.history, "supply": s.supply, "lastClose": s.last_close,
        }
    return {"as_of": as_of, "top3": [t.name for t in top3],
            "stocks": out, "news": news or {}}


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


def build_markdown(stocks: list[Stock], top3: list[Stock], as_of: str, mode: str,
                   news: Optional[dict] = None) -> str:
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
    out.append(f'title: "[{as_of}] 오늘의 AI 저평가 Top 3"')
    out.append(f"date: {as_of}T07:00:00+09:00")
    out.append("draft: false")
    out.append("categories: [\"AI투자\", \"데일리리포트\"]")
    out.append(f"tags: [{', '.join(json.dumps(t, ensure_ascii=False) for t in tags)}]")
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

    # --- 오늘의 AI 저평가 Top 3 ---
    out.append("## 오늘의 AI 저평가 Top 3")
    out.append("")
    out.append("| 순위 | 종목 | 섹션 | PER | PBR | EV/EBITDA | ROIC | PEG |")
    out.append("|:---:|:---|:---|---:|---:|---:|---:|---:|")
    for i, s in enumerate(top3, 1):
        out.append(
            f"| {i} | **{s.name}** ({s.ticker}) | {s.section} | "
            f"{fmt(s.per)} | {fmt(s.pbr)} | {fmt(s.ev_ebitda)} | "
            f"{fmt(s.roic, '%')} | {fmt(s.peg)} |"
        )
    out.append("")

    # --- Top 3 선정 근거 ---
    out.append("### 선정 근거")
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
        out.append("")

    # --- 투자 관점: 데이터 해석법 ---
    out.append("## 투자 관점: 데이터 해석법")
    out.append("")
    out.append("Top 3는 아래 기준으로 선정·검토됩니다. 무조건 오르는 종목은 없으나, "
               "재무적으로 우량하면서 시장의 주목은 아직 낮은 종목을 찾는 데 최적화되어 있습니다.")
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
    out.append("5. `종합점수 = 섹터가중 멀티플 할인 + ROIC 가점 + PEG 가점 + 안전마진` "
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
    top3 = pick_top3(stocks)
    if len(top3) < 3:
        print("[warn] 후보군이 3개 미만입니다. 종합점수 상위로 보충합니다.", file=sys.stderr)
        extra = sorted(stocks, key=lambda s: s.composite, reverse=True)
        for s in extra:
            if s not in top3:
                top3.append(s)
            if len(top3) == 3:
                break

    if args.mode == "sample":
        news = {sec: synth_news(sec) for sec in SECTION_ORDER}
    else:
        news = {sec: fetch_news(q) for sec, q in SECTION_NEWS_QUERY.items()}

    md = build_markdown(stocks, top3, as_of, args.mode, news)

    # 메인 대시보드 데이터
    LATEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PATH.write_text(
        json.dumps(build_dashboard(stocks, top3, as_of, news), ensure_ascii=False),
        encoding="utf-8")

    if args.stdout:
        print(md)
        return 0

    out_path = Path(args.out) if args.out else POSTS_DIR / f"{as_of}-daily-ai-top3.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ 리포트 생성: {out_path}")
    print(f"   Top 3: {', '.join(f'{s.name}({s.composite:.3f})' for s in top3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
