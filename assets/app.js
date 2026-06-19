"use strict";

const DATA = "./data";
const ALL = "__ALL__";
const $ = (sel) => document.querySelector(sel);
let CURRENT = null;

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
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

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
    CURRENT = await fetch(`${DATA}/${date}.json`, { cache: "no-store" }).then((r) => r.json());
    render(CURRENT);
  } catch (e) {
    $("#content").innerHTML = `<p class="error">${date} 데이터를 불러오지 못했습니다.</p>`;
  }
}

// ── 렌더 ───────────────────────────────────
function render(data) {
  $("#genAt").textContent = data.generated_at ? `· ${data.generated_at} 기준` : "";

  const order = ["sp500", "kospi200"].filter((k) => data.markets && data.markets[k]);
  $("#content").innerHTML =
    `<div class="markets">` + order.map((k) => marketShell(k, data.markets[k])).join("") + `</div>` +
    hedgeSection(data.hedge);

  order.forEach((k) => {
    const selEl = document.querySelector(`.sect-filter[data-key="${k}"]`);
    selEl.addEventListener("change", () => renderCards(k, selEl.value));
    renderCards(k, ALL);
  });

  renderMethod(data.methodology);
}

// AI 헤지 바스켓 — AI(반도체)와 반대로/무관하게 움직이는 자산군
function hedgeSection(h) {
  if (!h || !h.items || !h.items.length) return "";
  const cards = h.items.map((it) => {
    const neg = it.corr <= 0;
    const corrTxt = (it.corr > 0 ? "+" : "") + it.corr.toFixed(2);
    const ret = it.ret_6m != null ? (it.ret_6m > 0 ? "+" : "") + num(it.ret_6m, 1) + "%" : "—";
    return `
      <div class="hedge-card">
        <div class="hedge-top">
          <span class="hname">${it.name}<span class="ticker">${it.ticker}</span></span>
          <span class="corr ${neg ? "neg" : "pos"}">AI 상관 ${corrTxt}</span>
        </div>
        <div class="hedge-ret">최근 6개월 ${ret}</div>
        <div class="hedge-desc">${it.desc}</div>
      </div>`;
  }).join("");
  return `
    <section class="hedge">
      <div class="market-head">
        <h2>AI 헤지 바스켓</h2>
        <span class="count">${h.proxy} 대비 · ${h.period}</span>
      </div>
      <p class="hedge-note">AI(반도체)와 <b>반대로 또는 무관하게</b> 움직여 분산에 쓰이는 자산군입니다.
        상관계수가 낮을수록(음수일수록) 헤지 효과가 큽니다. 개별 종목 추천이 아니라 참고용 자산군이에요.</p>
      <div class="hedge-grid">${cards}</div>
      <p class="hedge-foot">한국에서 비슷한 성격: KT&amp;G·오리온(필수소비재), 한국가스공사(유틸리티), S-Oil·SK이노베이션(에너지), KODEX 골드선물(금).</p>
    </section>`;
}

function marketShell(key, m) {
  const counts = {};
  m.stocks.forEach((s) => { if (s.sector) counts[s.sector] = (counts[s.sector] || 0) + 1; });
  const opts = [`<option value="${ALL}">전체 업종 (${m.scored})</option>`].concat(
    Object.entries(counts).sort((a, b) => b[1] - a[1])
      .map(([sec, n]) => `<option value="${esc(sec)}">${esc(sec)} (${n})</option>`)
  );
  return `
    <section class="market">
      <div class="market-head">
        <h2>${m.label}</h2>
        <select class="sect-filter" data-key="${key}" aria-label="${m.label} 업종 필터">${opts.join("")}</select>
      </div>
      <div class="cards" id="cards-${key}"></div>
    </section>`;
}

function renderCards(key, sector) {
  const m = CURRENT.markets[key];
  const list = sector === ALL ? m.stocks : m.stocks.filter((s) => s.sector === sector);
  const top = list.slice(0, 3);
  const el = document.getElementById(`cards-${key}`);
  if (!top.length) {
    el.innerHTML = `<p class="empty">이 업종에는 조건(흑자·저평가)을 통과한 종목이 없어요.</p>`;
    return;
  }
  const note = sector === ALL
    ? `<p class="filter-note">전체 업종 — 구성종목 <b>${m.universe}개</b> 중 <b>${m.scored}개</b> 분석 <span>(적자·고PER&gt;60·고PBR&gt;20 제외)</span></p>`
    : `<p class="filter-note"><b>${esc(sector)}</b> 업종 저평가 Top ${top.length} <span>(${list.length}개 중)</span></p>`;
  el.innerHTML = note + top.map((s, i) => card(s, m.currency, m.label, i + 1)).join("");
}

function card(s, currency, label, rank) {
  const sector = s.sector ? `<span class="chip">${esc(s.sector)}</span>` : "";
  const negEq = s.neg_equity ? `<span class="chip warn">자본잠식</span>` : "";
  const fwd = s.forward_per ? `<span class="chip">선행PER ${num(s.forward_per, 1)}</span>` : "";
  return `
    <article class="card">
      <div class="card-top">
        <div class="rank r${rank}">${rank}</div>
        <div class="name-block">
          <div class="name">${s.name}<span class="ticker">${s.ticker}</span></div>
          <div class="sub">${sector}${negEq}${fwd}</div>
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
        ${s.neg_equity
          ? metric("PBR", `<span class="na">측정불가</span>`, null, "")
          : metric("PBR", mult(s.pbr), s.pbr_pct, "저평가")}
        ${s.neg_equity
          ? metric("ROE", `<span class="na">측정불가</span>`, null, "")
          : metric("ROE", s.roe != null ? num(s.roe, 1) + "%" : "—", s.roe_pct, "재무")}
        ${idioMetric(s)}
      </div>

      <details class="detail">
        <summary>숫자로 더 자세히</summary>
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
    ${meth.universe ? `<p>${meth.universe}</p>` : ""}
    ${gates ? `<ul class="gates">${gates}</ul>` : ""}
    ${meth.model ? `<p class="model"><code>${meth.model}</code></p>` : ""}
    <p>${meth.note || ""}</p>
    <table>${rows}</table>
    <p>필터: ${meth.filter || ""}</p>`;
}
