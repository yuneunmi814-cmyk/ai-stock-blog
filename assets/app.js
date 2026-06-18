"use strict";

const DATA = "./data";
const $ = (sel) => document.querySelector(sel);

// ── 포맷 헬퍼 ──────────────────────────────
const num = (v, d = 2) =>
  v == null || isNaN(v) ? "—" : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });

function price(v, currency) {
  if (v == null || isNaN(v)) return "—";
  return currency === "KRW"
    ? Math.round(v).toLocaleString("ko-KR") + "원"
    : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const mult = (v) => (v == null ? "—" : num(v, 2) + "배");
const pct = (v) => (v == null ? "—" : num(v, 2) + "%");

// ── 부트스트랩 ─────────────────────────────
init();

async function init() {
  try {
    const idx = await fetch(`${DATA}/index.json`, { cache: "no-store" }).then((r) => r.json());
    const dates = idx.dates || [];
    if (!dates.length) {
      $("#content").innerHTML = `<p class="error">아직 생성된 리포트가 없습니다. 첫 빌드를 기다려 주세요.</p>`;
      return;
    }
    const sel = $("#dateSelect");
    sel.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
    sel.value = idx.latest || dates[0];
    sel.addEventListener("change", () => load(sel.value));
    load(sel.value);
  } catch (e) {
    $("#content").innerHTML = `<p class="error">목록을 불러오지 못했습니다.</p>`;
  }
}

async function load(date) {
  $("#content").innerHTML = `<p class="loading">${date} 불러오는 중…</p>`;
  try {
    const data = await fetch(`${DATA}/${date}.json`, { cache: "no-store" }).then((r) => r.json());
    render(data);
  } catch (e) {
    $("#content").innerHTML = `<p class="error">${date} 데이터를 불러오지 못했습니다.</p>`;
  }
}

// ── 렌더 ───────────────────────────────────
function render(data) {
  $("#genAt").textContent = data.generated_at ? `· ${data.generated_at} 기준` : "";

  const order = ["sp500", "kospi200"];
  const sections = order
    .filter((k) => data.markets && data.markets[k])
    .map((k) => marketSection(data.markets[k]))
    .join("");
  $("#content").innerHTML = `<div class="markets">${sections}</div>`;

  renderMethod(data.methodology);
}

function marketSection(m) {
  const cards = (m.picks || []).map((s) => card(s, m.currency)).join("");
  return `
    <section class="market">
      <div class="market-head">
        <span class="flag">${m.flag || ""}</span>
        <h2>${m.label}</h2>
        <span class="count">${m.universe}개 중 ${m.scored}개 분석</span>
      </div>
      ${cards || `<p class="error">유효 종목이 없습니다.</p>`}
    </section>`;
}

function card(s, currency) {
  const r = s.rank || 1;
  const sector = s.sector ? `<span class="chip">${s.sector}</span>` : "";
  const fwd = s.forward_per ? `<span class="chip">선행PER ${num(s.forward_per, 1)}</span>` : "";

  return `
    <article class="card">
      <div class="card-top">
        <div class="rank r${r}">${r}</div>
        <div class="name-block">
          <div class="name">${s.name}<span class="ticker">${s.ticker}</span></div>
          <div class="sub">${sector}${fwd}</div>
        </div>
        <div class="price">
          <span class="now">${price(s.price, currency)}</span>
          <span class="cap">${s.market_cap || ""}</span>
        </div>
      </div>

      <div class="score-row">
        <span class="score-label">가치점수</span>
        <div class="score-bar"><div class="score-fill" style="width:${s.value_score || 0}%"></div></div>
        <span class="score-num">${num(s.value_score, 0)}</span>
      </div>

      <div class="metrics">
        ${metric("PER", mult(s.per), s.per_pct, "저평가")}
        ${metric("PBR", mult(s.pbr), s.pbr_pct, "저평가")}
        ${metric("ROE", s.roe != null ? num(s.roe, 1) + "%" : "—", s.roe_pct, "재무")}
        ${idioMetric(s)}
      </div>

      <details class="detail">
        <summary>상세 정보</summary>
        ${s.rationale ? `<p class="rationale">${s.rationale}</p>` : ""}
        <table class="facts">
          ${row("배당수익률", s.div_yield != null ? pct(s.div_yield) : null)}
          ${row("β (베타, 시장민감도)", s.beta != null ? num(s.beta, 2) : null)}
          ${row("6개월 수익률", s.ret_6m != null ? (s.ret_6m > 0 ? "+" : "") + num(s.ret_6m, 1) + "%" : null)}
          ${row("비체계적 수익률 (시장대비)", s.idio_6m != null ? (s.idio_6m > 0 ? "+" : "") + num(s.idio_6m, 1) + "%p" : null)}
          ${row("EPS (주당순이익)", s.eps != null ? num(s.eps, 2) : null)}
          ${row("BPS (주당순자산)", s.bps != null ? num(s.bps, 2) : null)}
          ${row("선행 PER", s.forward_per != null ? num(s.forward_per, 1) + "배" : null)}
          ${row("부채비율 (D/E)", s.debt_to_equity != null ? num(s.debt_to_equity, 1) + "%" : null)}
          ${row("순이익률", s.profit_margin != null ? num(s.profit_margin, 1) + "%" : null)}
          ${row("52주 최고", s.high_52w != null ? price(s.high_52w, currency) : null)}
          ${row("52주 최저", s.low_52w != null ? price(s.low_52w, currency) : null)}
          ${row("외국인 보유", s.foreign_rate != null ? num(s.foreign_rate, 2) + "%" : null)}
          ${row("시가총액", s.market_cap || null)}
        </table>
        ${s.link ? `<a class="ext" href="${s.link}" target="_blank" rel="noopener">원문 보기 ↗</a>` : ""}
      </details>
    </article>`;
}

function metric(k, v, p, tag) {
  const pctLine = p != null ? `<div class="pct">${tag} 상위 ${Math.max(1, Math.round(100 - p))}%</div>` : "";
  return `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div>${pctLine}</div>`;
}

// 비체계적(고유) 과매도 — 핵심 차별 지표
function idioMetric(s) {
  if (s.idio_6m == null) return `<div class="metric"><div class="k">비체계적</div><div class="v">—</div></div>`;
  const cls = s.idio_6m <= 0 ? "v down" : "v up";
  const val = (s.idio_6m > 0 ? "+" : "") + num(s.idio_6m, 0) + "%p";
  const sub = s.idio_pct != null ? `<div class="pct">과매도 상위 ${Math.max(1, Math.round(100 - s.idio_pct))}%</div>` : "";
  return `<div class="metric idio"><div class="k">비체계적</div><div class="${cls}">${val}</div>${sub}</div>`;
}

function row(k, v) {
  return v == null ? "" : `<tr><td>${k}</td><td>${v}</td></tr>`;
}

function renderMethod(meth) {
  if (!meth) return;
  const w = meth.weights || {};
  const gates = (meth.gates || []).map((g) => `<li>${g}</li>`).join("");
  const rows = Object.entries(w)
    .map(([k, v]) => `<tr><td>${k}</td><td><code>${Math.round(v * 100)}%</code></td></tr>`)
    .join("");
  $("#methodBody").innerHTML = `
    ${meth.title ? `<p class="method-title"><b>${meth.title}</b></p>` : ""}
    ${gates ? `<ul class="gates">${gates}</ul>` : ""}
    ${meth.model ? `<p class="model"><code>${meth.model}</code></p>` : ""}
    <p>${meth.note || ""}</p>
    <table>${rows}</table>
    <p>필터: ${meth.filter || ""}</p>`;
}
