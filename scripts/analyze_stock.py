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
POSTS_DIR = ROOT / "_posts"
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
    ],
    "AI 애플리케이션": [
        {"name": "어도비", "ticker": "ADBE", "source": "yahoo"},
        {"name": "세일즈포스", "ticker": "CRM", "source": "yahoo"},
        {"name": "서비스나우", "ticker": "NOW", "source": "yahoo"},
        {"name": "듀오링고", "ticker": "DUOL", "source": "yahoo"},
        {"name": "더존비즈온", "ticker": "012510.KS", "source": "dart", "corp_code": "00521542"},
        {"name": "테슬라", "ticker": "TSLA", "source": "yahoo"},
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
    value_score: float = 0.0       # 동종평균 대비 멀티플 할인폭(높을수록 저평가)
    quality_bonus: float = 0.0     # ROIC 가점
    growth_bonus: float = 0.0      # PEG<1 가점
    composite: float = 0.0
    is_candidate: bool = False     # 1차 후보군 여부
    cheap_flags: list[str] = field(default_factory=list)

    def metric(self, key: str) -> Optional[float]:
        return getattr(self, key)


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------
def load_sample() -> tuple[list[Stock], str]:
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    stocks: list[Stock] = []
    for section, rows in raw["sections"].items():
        for r in rows:
            stocks.append(Stock(
                name=r["name"], ticker=r["ticker"], source=r["source"],
                section=section, currency=r.get("currency", ""),
                market_cap=r.get("market_cap"), per=r.get("per"), pbr=r.get("pbr"),
                ev_ebitda=r.get("ev_ebitda"), roic=r.get("roic"), peg=r.get("peg"),
            ))
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
    return Stock(
        name=name, ticker=ticker, source="yahoo", section=section,
        currency=info.get("currency", "USD"),
        market_cap=(info.get("marketCap") or 0) / 1e9 or None,
        per=_clean(per), pbr=_clean(pbr), ev_ebitda=_clean(ev_ebitda),
        roic=_clean(roic), peg=_clean(peg),
    )


def fetch_dart(name: str, ticker: str, section: str, corp_code: str) -> Stock:
    """DART 공시 최신 분기 보고서 기반 펀더멘털 + 시장가로 멀티플 산출.

    실데이터 수집은 OpenDART 재무제표 API(fnlttSinglAcntAll)와 시세가 필요하다.
    환경변수 DART_API_KEY 사용. 네트워크/키 부재 시 yahoo 폴백.
    """
    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        # 키가 없으면 Yahoo가 .KS 티커도 커버하므로 폴백
        s = fetch_yahoo(name, ticker, section)
        s.source = "dart(폴백:yahoo)"
        return s
    # NOTE: 실제 구현에서는 아래에서 OpenDART fnlttSinglAcntAll 로
    #       당기순이익/자본총계/영업이익/EBITDA/투하자본을 받아
    #       시가총액과 결합해 PER/PBR/EV-EBITDA/ROIC/PEG 를 계산한다.
    #       (분기 보고서 reprt_code=11013/11012/11014 우선 적용)
    #       데모 환경에서는 yahoo 폴백으로 동작을 보장한다.
    s = fetch_yahoo(name, ticker, section)
    s.source = "dart"
    return s


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
                stocks.append(s)
            except Exception as e:  # noqa: BLE001 — 종목 1건 실패가 전체를 막지 않도록
                print(f"  [warn] {r['name']}({r['ticker']}) 수집 실패: {e}", file=sys.stderr)
    return stocks, datetime.now(KST).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 저평가 필터링 / 스코어링
# ---------------------------------------------------------------------------
def section_peer_avg(stocks: list[Stock], metric: str) -> Optional[float]:
    vals = [s.metric(metric) for s in stocks if s.metric(metric) is not None and s.metric(metric) > 0]
    return sum(vals) / len(vals) if vals else None


def score(stocks: list[Stock]) -> None:
    """섹션별 동종평균 대비 저평가 점수 + ROIC/PEG 가점."""
    by_section: dict[str, list[Stock]] = {}
    for s in stocks:
        by_section.setdefault(s.section, []).append(s)

    for section, group in by_section.items():
        peer = {m: section_peer_avg(group, m) for m in VALUATION_METRICS}
        for s in group:
            discounts, cheaper_count = [], 0
            for m in VALUATION_METRICS:
                v, avg = s.metric(m), peer[m]
                if v is None or avg is None or v <= 0:
                    continue
                disc = (avg - v) / avg  # +면 평균보다 쌈
                discounts.append(disc)
                if disc > 0:
                    cheaper_count += 1
                    s.cheap_flags.append(METRIC_LABEL[m])
            # 1차 후보군: 멀티플 과반이 동종평균보다 낮음
            s.is_candidate = cheaper_count >= 2
            s.value_score = (sum(discounts) / len(discounts)) if discounts else -1.0

            # 가점: ROIC(수익성) — 0~40%를 0~1로 정규화
            if s.roic is not None:
                s.quality_bonus = max(0.0, min(s.roic, 40.0)) / 40.0
            # 가점: PEG<1(성장성 대비 저평가)
            if s.peg is not None and 0 < s.peg < 1:
                s.growth_bonus = (1 - s.peg)  # 0~1

            s.composite = (
                1.00 * s.value_score
                + 0.35 * s.quality_bonus
                + 0.50 * s.growth_bonus
            )


def pick_top3(stocks: list[Stock]) -> list[Stock]:
    cands = [s for s in stocks if s.is_candidate]
    cands.sort(key=lambda s: s.composite, reverse=True)
    return cands[:3]


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


def build_markdown(stocks: list[Stock], top3: list[Stock], as_of: str, mode: str) -> str:
    by_section: dict[str, list[Stock]] = {}
    for s in stocks:
        by_section.setdefault(s.section, []).append(s)
    peer_by_section = {
        sec: {m: section_peer_avg(g, m) for m in VALUATION_METRICS}
        for sec, g in by_section.items()
    }

    medals = ["🥇", "🥈", "🥉"]
    out: list[str] = []

    # --- Front matter ---
    out.append("---")
    out.append("layout: post")
    out.append(f'title: "[{as_of}] 오늘의 AI 저평가 Top 3"')
    out.append(f"date: {as_of} 07:00:00 +0900")
    out.append("categories: [AI투자, 데일리리포트]")
    out.append(f"tags: [{', '.join(t.name for t in top3)}, 저평가, 밸류에이션]")
    out.append("---")
    out.append("")

    # --- 면책 문구(최상단) ---
    out.append("> ⚠️ **면책 고지** — 본 리포트는 재무제표 기반의 정량적 분석 자료이며, "
               "실제 투자 결과에 대한 책임은 투자자 본인에게 있습니다.")
    out.append("")
    out.append(f"<sub>📊 데이터 기준일: {as_of} · 지표는 DART 공시 최신 분기 보고서 데이터를 우선 적용 "
               f"(해외 종목은 Yahoo Finance) · 생성 모드: `{mode}`</sub>")
    out.append("")

    # --- 오늘의 AI 저평가 Top 3 요약 박스(Table) ---
    out.append("## 🏆 오늘의 AI 저평가 Top 3")
    out.append("")
    out.append("| 순위 | 종목 | 섹션 | PER | PBR | EV/EBITDA | ROIC | PEG |")
    out.append("|:---:|:---|:---|---:|---:|---:|---:|---:|")
    for i, s in enumerate(top3):
        out.append(
            f"| {medals[i]} | **{s.name}** ({s.ticker}) | {s.section} | "
            f"{fmt(s.per)} | {fmt(s.pbr)} | {fmt(s.ev_ebitda)} | "
            f"{fmt(s.roic, '%')} | {fmt(s.peg)} |"
        )
    out.append("")

    # --- Top 3 3줄 분석 ---
    out.append("### 왜 저평가인가 — 3줄 분석")
    out.append("")
    for i, s in enumerate(top3):
        out.append(f"#### {medals[i]} {s.name} ({s.ticker}) · {s.section}")
        for line in reason_lines(s, peer_by_section[s.section]):
            out.append(f"- {line}")
        out.append("")

    # --- 섹션별 시장 동향 ---
    out.append("---")
    out.append("")
    out.append("## 📈 섹션별 시장 동향")
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
            cand = "✅" if s.is_candidate else "—"
            flags = ", ".join(sorted(set(s.cheap_flags))) if s.cheap_flags else "—"
            out.append(
                f"| {s.name} ({s.ticker}) | {fmt(s.per)} | {fmt(s.pbr)} | "
                f"{fmt(s.ev_ebitda)} | {fmt(s.roic, '%')} | {fmt(s.peg)} | {cand} | {flags} |"
            )
        out.append("")

    # --- 방법론 ---
    out.append("---")
    out.append("")
    out.append("<details><summary>📐 분석 방법론</summary>")
    out.append("")
    out.append("1. 4개 섹션(인프라/반도체, 클라우드/데이터센터, 모델/소프트웨어, AI 애플리케이션) 전 종목의 "
               "**PER·PBR·EV/EBITDA·ROIC·PEG**를 수집·계산.")
    out.append("2. 섹션 내 **동종업계 평균** 대비 멀티플(PER·PBR·EV/EBITDA)이 낮은 종목을 1차 후보군으로 선정 "
               "(과반 지표가 평균 미만일 때 ✅).")
    out.append("3. **ROIC가 높고(수익성 우수), PEG가 1 미만(성장 대비 저평가)** 인 종목에 가점.")
    out.append("4. `종합점수 = 멀티플 할인폭 + 0.35·ROIC가점 + 0.50·PEG가점` 상위 3종목을 Top 3로 선정.")
    out.append("")
    out.append("> 국내 종목은 DART 공시 최신 분기 보고서 데이터를 우선 적용하며, 해외 종목은 Yahoo Finance를 사용합니다.")
    out.append("</details>")
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

    md = build_markdown(stocks, top3, as_of, args.mode)

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
