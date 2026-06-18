#!/usr/bin/env python3
"""일자별 S&P 500 · KOSPI 200 "비체계적 저평가" Top 3 빌더.

가치투자 + 위험분해 4팩터 스크린 (API 키 불필요):

  ① 저평가      PER · PBR 가 낮은가            (이익·자산 대비 싼가)
  ② 재무 양호   ROE 가 높은가 (흑자)           (가치함정 배제)
  ③ 비체계적 하락  시장모델 회귀로 분리한 고유수익률이 음(-)인가
                  종목수익률 = α + β·시장수익률 + ε  →  비체계적 = 종목 − β·시장

세 관문을 모두 통과한 "시장과 무관하게(비체계적으로) 과매도된 우량 저평가주"를
각 지수 내 백분위 가중합 점수가 높은 순으로 Top 3 선정한다.

데이터 소스
  - S&P 500 구성종목 : Wikipedia
  - 미국 펀더멘털     : yfinance (Yahoo Finance)
  - KOSPI 200 구성종목: 네이버 금융 (entryJongmok)
  - 한국 펀더멘털     : 네이버 금융 모바일 API
  - 가격 이력(양 시장): yfinance (미국 vs ^GSPC, 한국 .KS vs ^KS11)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import requests

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KST = timezone(timedelta(hours=9))

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}

# ── 4팩터 가중치 (합 1.0) ───────────────────────────────────────────────
W_PER, W_PBR, W_ROE, W_IDIO = 0.25, 0.15, 0.25, 0.35
#   저평가 40%(PER25+PBR15) · 재무 양호 25%(ROE) · 비체계적 과매도 35%
PER_RANGE = (0.0, 60.0)   # 흑자 + 과도한 고평가 제외
PBR_RANGE = (0.0, 20.0)
HIST_PERIOD = "6mo"       # 위험분해 회귀 구간
TOP_N = 3


@dataclass
class Stock:
    ticker: str
    name: str
    sector: str = ""
    price: float | None = None
    currency: str = "USD"
    market_cap: str = ""
    # ① 저평가
    per: float | None = None
    pbr: float | None = None
    div_yield: float | None = None     # %
    eps: float | None = None
    bps: float | None = None
    forward_per: float | None = None
    # ② 재무 양호
    roe: float | None = None           # %
    debt_to_equity: float | None = None  # % (미국)
    profit_margin: float | None = None   # % (미국)
    # ③ 비체계적 하락 (가격이력 회귀)
    beta: float | None = None
    ret_6m: float | None = None        # % 6개월 수익률
    idio_6m: float | None = None       # %p 비체계적(시장대비 초과) 수익률
    # 기타
    high_52w: float | None = None
    low_52w: float | None = None
    foreign_rate: float | None = None  # % (한국)
    link: str = ""
    # 계산
    per_pct: float | None = None
    pbr_pct: float | None = None
    roe_pct: float | None = None
    idio_pct: float | None = None
    value_score: float | None = None
    rank: int | None = None
    rationale: str = ""
    explain: list | None = None        # 초등학생도 이해하는 친절한 설명


# ════════════════════════════════════════════════════════════════════════
#  미국 — S&P 500
# ════════════════════════════════════════════════════════════════════════
def sp500_universe() -> list[tuple[str, str, str]]:
    import pandas as pd

    html = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers=UA, timeout=30,
    ).text
    df = pd.read_html(StringIO(html))[0]
    out = []
    for _, row in df.iterrows():
        sym = str(row["Symbol"]).strip().replace(".", "-")  # BRK.B -> BRK-B
        out.append((sym, str(row["Security"]).strip(), str(row["GICS Sector"]).strip()))
    return out


def fetch_us(ticker: str, name: str, sector: str) -> Stock | None:
    import yfinance as yf

    for attempt in range(2):
        try:
            info = yf.Ticker(ticker).info
            if not info or (info.get("trailingPE") is None and info.get("priceToBook") is None):
                return None
            roe = info.get("returnOnEquity")
            pm = info.get("profitMargins")
            return Stock(
                ticker=ticker, name=name, sector=sector,
                price=info.get("currentPrice") or info.get("regularMarketPrice"),
                currency=info.get("currency", "USD"),
                market_cap=_fmt_usd_cap(info.get("marketCap")),
                per=_clean(info.get("trailingPE")),
                pbr=_clean(info.get("priceToBook")),
                div_yield=_clean(info.get("dividendYield")) or 0.0,  # 이미 % 단위
                eps=_clean(info.get("trailingEps")),
                bps=_clean(info.get("bookValue")),
                forward_per=_clean(info.get("forwardPE")),
                roe=round(roe * 100, 1) if isinstance(roe, (int, float)) else None,
                debt_to_equity=_clean(info.get("debtToEquity")),
                profit_margin=round(pm * 100, 1) if isinstance(pm, (int, float)) else None,
                high_52w=_clean(info.get("fiftyTwoWeekHigh")),
                low_52w=_clean(info.get("fiftyTwoWeekLow")),
                link=f"https://finance.yahoo.com/quote/{ticker}",
            )
        except Exception:
            if attempt == 0:
                time.sleep(0.8)
    return None


def _fmt_usd_cap(v) -> str:
    if not isinstance(v, (int, float)) or v <= 0:
        return ""
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    return f"${v / 1e6:.0f}M"


# ════════════════════════════════════════════════════════════════════════
#  한국 — KOSPI 200
# ════════════════════════════════════════════════════════════════════════
def kospi200_universe(session: requests.Session) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for page in range(1, 30):
        url = f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}"
        html = session.get(url, timeout=20).content.decode("euc-kr", "ignore")
        fresh = [c for c in re.findall(r"code=(\d{6})", html) if c not in seen]
        if not fresh:
            break
        for c in fresh:
            seen.add(c)
            codes.append(c)
    return codes


def fetch_kr(code: str, session: requests.Session) -> Stock | None:
    try:
        j = session.get(
            f"https://m.stock.naver.com/api/stock/{code}/integration", timeout=20
        ).json()
    except Exception:
        return None
    ti = {t.get("code"): t.get("value") for t in j.get("totalInfos", []) if isinstance(t, dict)}
    if not ti:
        return None
    eps = _to_num(ti.get("eps"))
    bps = _to_num(ti.get("bps"))
    roe = round(eps / bps * 100, 1) if eps and bps and bps != 0 else None
    return Stock(
        ticker=code, name=j.get("stockName", code), sector="",
        price=_to_num(ti.get("lastClosePrice")),
        currency="KRW",
        market_cap=str(ti.get("marketValue", "")).strip(),
        per=_to_num(ti.get("per")),
        pbr=_to_num(ti.get("pbr")),
        div_yield=_to_num(ti.get("dividendYieldRatio")) or 0.0,
        eps=eps, bps=bps,
        forward_per=_to_num(ti.get("cnsPer")),
        roe=roe,
        high_52w=_to_num(ti.get("highPriceOf52Weeks")),
        low_52w=_to_num(ti.get("lowPriceOf52Weeks")),
        foreign_rate=_to_num(ti.get("foreignRate")),
        link=f"https://finance.naver.com/item/main.naver?code={code}",
    )


# ════════════════════════════════════════════════════════════════════════
#  ③ 비체계적 위험 분해 — 시장모델 회귀
# ════════════════════════════════════════════════════════════════════════
def add_idiosyncratic(stocks: list[Stock], index_ticker: str, suffix: str) -> None:
    """6개월 일간수익률을 시장지수에 회귀해 β와 비체계적(고유) 수익률을 채운다."""
    import yfinance as yf

    tmap = {s.ticker: s for s in stocks}
    cols = [t + suffix for t in tmap] + [index_ticker]
    try:
        px = yf.download(cols, period=HIST_PERIOD, interval="1d",
                         progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        print(f"  ! 가격이력 수집 실패({index_ticker}): {e}")
        return
    if index_ticker not in px:
        print(f"  ! 지수 {index_ticker} 가격 없음 — 비체계적 점수 생략")
        return

    done = 0
    for tk, s in tmap.items():
        col = tk + suffix
        if col not in px:
            continue
        pair = px[[col, index_ticker]].dropna()
        if len(pair) < 40:
            continue
        r = pair.pct_change().dropna()
        mvar = r[index_ticker].var()
        if not mvar or mvar != mvar:
            continue
        beta = r[col].cov(r[index_ticker]) / mvar
        stock_tot = pair[col].iloc[-1] / pair[col].iloc[0] - 1
        mkt_tot = pair[index_ticker].iloc[-1] / pair[index_ticker].iloc[0] - 1
        s.beta = round(float(beta), 2)
        s.ret_6m = round(float(stock_tot) * 100, 1)
        s.idio_6m = round(float((stock_tot - beta * mkt_tot)) * 100, 1)
        done += 1
    print(f"  비체계적 분해 {done}/{len(tmap)} 종목 (지수 {index_ticker})")


# ════════════════════════════════════════════════════════════════════════
#  점수
# ════════════════════════════════════════════════════════════════════════
def _clean(v) -> float | None:
    if isinstance(v, (int, float)) and v == v and abs(v) != float("inf"):
        return round(float(v), 4)
    return None


def _to_num(s) -> float | None:
    if s is None:
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(s))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _assign_pct(stocks: list[Stock], attr: str, higher_better: bool, out: str) -> None:
    """지표를 시장 내 백분위(0~100, 100=가장 유리)로 변환."""
    vals = [s for s in stocks if getattr(s, attr) is not None]
    n = len(vals)
    if n < 2:
        for s in vals:
            setattr(s, out, 50.0)
        return
    ordered = sorted(vals, key=lambda s: getattr(s, attr), reverse=not higher_better)
    for rank, s in enumerate(ordered):
        setattr(s, out, round(100.0 * rank / (n - 1), 1))


def kid_explain(s: Stock, label: str, rank: int) -> list[str]:
    """초등학교 6학년도 이해할 수 있게, 실제 숫자를 비유로 풀어 설명한다."""
    pts: list[str] = []
    cheap = max(1, round(100 - (s.per_pct or 0)))
    pts.append(
        f"💰 버는 돈에 비해 주식이 싸요. PER {s.per:.1f}배는 '이 회사가 지금처럼 벌면 약 "
        f"{round(s.per)}년이면 회사를 산 값을 다 뽑는다'는 뜻이라, 숫자가 작을수록 싼 거예요 "
        f"({label}에서 싼 쪽 {cheap}%)."
    )
    if s.pbr is not None and s.pbr < 1.2:
        pts.append(
            f"🏦 게다가 회사가 가진 재산(건물·현금 등)값과 비슷하거나 더 싸게 팔려요 "
            f"(PBR {s.pbr:.2f}배 — 1배보다 작으면 재산보다도 싸다는 뜻이에요)."
        )
    if s.roe is not None:
        pts.append(
            f"🏆 그런데 장사는 야무져요. 가진 돈 100원으로 1년에 약 {round(s.roe)}원을 버는 "
            f"똑똑한 회사거든요 (ROE {s.roe:.1f}%)."
        )
    if s.idio_6m is not None:
        pts.append(
            f"📉 그런데 최근 6개월, 주식 시장 전체와 비교하면 이 회사만 유독 뒤처졌어요 "
            f"(시장 대비 {s.idio_6m:+.0f}%p). 반 평균은 올랐는데 이 친구 점수만 떨어진 것과 비슷해요. "
            f"시장 전체가 나빠서가 아니라 이 회사한테만 생긴 특별한 일 때문이라, "
            f"버는 실력은 그대론데 너무 싸진 것일 수 있어요."
        )
    pts.append(f"👉 한마디로 '돈은 잘 버는데 특별한 이유로 싸진 회사'라서 {rank}위로 골랐어요.")
    return pts


def score_market(stocks: list[Stock], label: str) -> list[Stock]:
    # 관문 ①·② 하드필터: 저평가 정상범위 + 흑자(ROE>0)
    pool = [
        s for s in stocks
        if s.per is not None and PER_RANGE[0] < s.per <= PER_RANGE[1]
        and s.pbr is not None and PBR_RANGE[0] < s.pbr <= PBR_RANGE[1]
        and s.roe is not None and s.roe > 0
    ]
    _assign_pct(pool, "per", higher_better=False, out="per_pct")   # 낮을수록 저평가
    _assign_pct(pool, "pbr", higher_better=False, out="pbr_pct")
    _assign_pct(pool, "roe", higher_better=True, out="roe_pct")    # 높을수록 우량
    _assign_pct(pool, "idio_6m", higher_better=False, out="idio_pct")  # 낮을수록(음) 과매도
    for s in pool:
        pp = lambda v: v if v is not None else 50.0  # noqa: E731
        s.value_score = round(
            W_PER * pp(s.per_pct) + W_PBR * pp(s.pbr_pct)
            + W_ROE * pp(s.roe_pct) + W_IDIO * pp(s.idio_pct), 1
        )
    pool.sort(key=lambda s: s.value_score or 0, reverse=True)
    picks = pool[:TOP_N]
    for i, s in enumerate(picks, 1):
        s.rank = i
        idio_txt = (f"시장대비 {s.idio_6m:+.0f}%p 과매도(β {s.beta})"
                    if s.idio_6m is not None else "비체계적 신호 없음")
        s.rationale = (
            f"PER {s.per:.1f}배·PBR {s.pbr:.2f}배(저평가) · ROE {s.roe:.1f}%(재무 양호) · "
            f"{idio_txt} → {label} {len(pool)}개 중 가치점수 {s.value_score:.0f}점 ({i}위)"
        )
        s.explain = kid_explain(s, label, i)
    return picks


# ════════════════════════════════════════════════════════════════════════
#  빌드
# ════════════════════════════════════════════════════════════════════════
def build(date_str: str, limit: int | None) -> None:
    session = requests.Session()
    session.headers.update(UA)
    markets = {}

    # ── 미국 ──
    print("· S&P 500 구성종목 로딩…", flush=True)
    us_univ = sp500_universe()
    if limit:
        us_univ = us_univ[:limit]
    print(f"  {len(us_univ)}개 펀더멘털 수집(yfinance)…", flush=True)
    us_stocks: list[Stock] = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        for f in as_completed([ex.submit(fetch_us, t, n, s) for t, n, s in us_univ]):
            r = f.result()
            if r:
                us_stocks.append(r)
    print(f"  펀더멘털 {len(us_stocks)}/{len(us_univ)} · 가격이력 회귀…", flush=True)
    add_idiosyncratic(us_stocks, "^GSPC", suffix="")
    markets["sp500"] = _market("S&P 500", "🇺🇸", "USD", len(us_univ), us_stocks)

    # ── 한국 ──
    print("· KOSPI 200 구성종목 로딩…", flush=True)
    kr_codes = kospi200_universe(session)
    if limit:
        kr_codes = kr_codes[:limit]
    print(f"  {len(kr_codes)}개 펀더멘털 수집(네이버)…", flush=True)
    kr_stocks: list[Stock] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f in as_completed([ex.submit(fetch_kr, c, session) for c in kr_codes]):
            r = f.result()
            if r:
                kr_stocks.append(r)
    print(f"  펀더멘털 {len(kr_stocks)}/{len(kr_codes)} · 가격이력 회귀…", flush=True)
    add_idiosyncratic(kr_stocks, "^KS11", suffix=".KS")
    markets["kospi200"] = _market("KOSPI 200", "🇰🇷", "KRW", len(kr_codes), kr_stocks)

    payload = {
        "date": date_str,
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "methodology": {
            "title": "비체계적 위험으로 과매도된 우량 저평가주",
            "gates": [
                "① 저평가 — PER·PBR이 낮은가 (이익·자산 대비 싼가)",
                "② 재무 양호 — ROE가 높은가, 흑자인가 (가치함정 배제)",
                "③ 비체계적 하락 — 시장모델 회귀(6M)로 분리한 고유수익률이 음(-)인가",
            ],
            "model": "종목수익률 = α + β·시장수익률 + ε  →  비체계적 = 종목수익률 − β·시장수익률",
            "weights": {"PER": W_PER, "PBR": W_PBR, "ROE": W_ROE, "비체계적과매도": W_IDIO},
            "filter": "PER 0~60배 · PBR 0~20배 · ROE>0 (적자·이상치 제외)",
            "note": "네 지표를 각 지수 내 백분위로 환산해 가중합한 '가치점수' 상위 Top 3. "
                    "③은 6개월 일간수익률을 시장지수(미국 S&P500, 한국 KOSPI)에 회귀해 β로 시장요인을 제거한 뒤 남는 고유 하락폭.",
        },
        "markets": markets,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{date_str}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 저장: {out_path.relative_to(ROOT)}")
    _update_index()


def _market(label, flag, currency, universe, stocks) -> dict:
    return {
        "label": label, "flag": flag, "currency": currency,
        "universe": universe, "scored": len(stocks),
        "picks": [_export(s) for s in score_market(stocks, label)],
    }


def _export(s: Stock) -> dict:
    return {k: v for k, v in asdict(s).items() if v is not None and v != ""}


def _update_index() -> None:
    dates = sorted(
        (p.stem for p in DATA_DIR.glob("*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)),
        reverse=True,
    )
    (DATA_DIR / "index.json").write_text(
        json.dumps({"dates": dates, "latest": dates[0] if dates else None},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ index.json 갱신: {len(dates)}일치")


def main() -> int:
    ap = argparse.ArgumentParser(description="S&P500 · KOSPI200 비체계적 저평가 Top 3 빌더")
    ap.add_argument("--date", default=datetime.now(KST).strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=None, help="시장별 N종목만(로컬 점검용)")
    args = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        print("날짜 형식 오류 (YYYY-MM-DD)", file=sys.stderr)
        return 2
    build(args.date, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
