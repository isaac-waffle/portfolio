/* Forecast Lab: loads forecast.json (data branch, CDN) with a bundled snapshot fallback. */
(function () {
  "use strict";
  var LIVE_URL = "https://raw.githubusercontent.com/isaac-waffle/portfolio/data/forecast.json";
  var SNAPSHOT_URL = "data/forecast-sample.json";
  var POLL_MS = 60000;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var S = { d: null, h: "5m", snapshot: false, featured: null };
  var HZ = { "5m": "Next 5 min", "1h": "Next hour", "1d": "Next session" };

  var fmtPx = function (v) { return v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  var fmtPct = function (v, d) { if (v == null || !isFinite(v)) return "—"; d = d == null ? 2 : d; var s = (v * 100).toFixed(d); return (v > 0 ? "+" : v < 0 ? "−" : "") + s.replace("-", "") + "%"; };
  var cls = function (v) { return v > 0 ? "up" : v < 0 ? "down" : "flat"; };
  var pctTxt = function (p) { return (p * 100).toFixed(1) + "%"; };
  function ago(iso) {
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago";
  }
  function lean(p) { var e = p - 0.5; return Math.abs(e) < 0.005 ? ["Neutral", "flat"] : e > 0 ? ["Lean up", "up"] : ["Lean down", "down"]; }

  /* ---------- data ---------- */
  function get(url) { return fetch(url, { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function load() {
    return get(LIVE_URL + "?t=" + Math.floor(Date.now() / 60000))
      .then(function (d) { S.snapshot = false; return d; })
      .catch(function () { S.snapshot = true; return get(SNAPSHOT_URL); })
      .then(function (d) { S.d = d; render(); })
      .catch(function () { $("#labStatus").innerHTML = '<span class="dot off"></span>Forecast data unavailable right now'; });
  }

  /* ---------- charts ---------- */
  function sparkSVG(row, f, w, h, opts) {
    opts = opts || {};
    var sp = (row.spark || []).map(function (x) { return x[1]; });
    if (!sp.length) return "";
    var proj = opts.proj == null ? 0.22 : opts.proj, pw = w * (1 - proj), pad = 4;
    var lo = Math.min.apply(null, sp), hi = Math.max.apply(null, sp);
    if (f) { lo = Math.min(lo, f.lo * (f.last ? row.px / f.last : 1)); hi = Math.max(hi, f.hi * (f.last ? row.px / f.last : 1)); }
    if (row.prev_close && opts.prev) { lo = Math.min(lo, row.prev_close); hi = Math.max(hi, row.prev_close); }
    var span = (hi - lo) || 1, y = function (v) { return pad + (hi - v) / span * (h - pad * 2); };
    var x = function (i) { return sp.length > 1 ? i / (sp.length - 1) * pw : 0; };
    var path = sp.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); }).join("");
    var up = row.chg >= 0, col = up ? "var(--up)" : "var(--down)";
    var s = "";
    if (opts.prev && row.prev_close) s += '<line x1="0" x2="' + w + '" y1="' + y(row.prev_close).toFixed(1) + '" y2="' + y(row.prev_close).toFixed(1) + '" stroke="var(--ink-4)" stroke-dasharray="2 4"/>';
    s += '<path d="' + path + "L" + pw.toFixed(1) + "," + h + "L0," + h + 'Z" fill="' + col + '" opacity=".08"/>';
    s += '<path d="' + path + '" fill="none" stroke="' + col + '" stroke-width="' + (opts.sw || 1.5) + '" stroke-linejoin="round"/>';
    if (f) {
      // rebase the forecast onto the latest price so every horizon starts from "now"
      var k = f.last ? row.px / f.last : 1, lx = pw, ly = y(row.px), ex = w - 2;
      var pc = f.p >= 0.5 ? "var(--accent)" : "var(--down)";
      s += '<path d="M' + lx + "," + ly + "L" + ex + "," + y(f.hi * k).toFixed(1) + "L" + ex + "," + y(f.lo * k).toFixed(1) + 'Z" fill="' + pc + '" opacity=".16"/>';
      s += '<line x1="' + lx + '" y1="' + ly + '" x2="' + ex + '" y2="' + y(f.px * k).toFixed(1) + '" stroke="' + pc + '" stroke-width="1.5" stroke-dasharray="3 3"/>';
      s += '<circle cx="' + ex + '" cy="' + y(f.px * k).toFixed(1) + '" r="2.6" fill="' + pc + '"/>';
      s += '<circle cx="' + lx + '" cy="' + ly + '" r="2.6" fill="var(--ink)"/>';
    }
    return s;
  }
  function pbar(p) {
    var e = p - 0.5, w = Math.min(50, Math.abs(e) / 0.05 * 50);   // ±5pp fills the half-bar
    return '<div class="pbar"><i style="left:' + (e >= 0 ? 50 : 50 - w) + "%;width:" + w + "%;background:" + (e >= 0 ? "var(--up)" : "var(--down)") + '"></i></div>';
  }

  /* ---------- render ---------- */
  function rowsSorted() {
    var R = S.d.rows;
    return Object.keys(R).filter(function (t) { return R[t].px != null; }).map(function (t) { return Object.assign({ t: t }, R[t]); })
      .sort(function (a, b) { var fa = a.f[S.h], fb = b.f[S.h]; return Math.abs((fb ? fb.p : .5) - .5) - Math.abs((fa ? fa.p : .5) - .5); });
  }
  function render() {
    var d = S.d, m = d.market || {};
    var live = !S.snapshot && m.open && (Date.now() - new Date(d.updated_utc).getTime()) < 40 * 60000;
    var st = S.snapshot ? "Snapshot · " + new Date(d.updated_utc).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
      : (m.status || "") + ' <span class="sep">·</span> updated ' + ago(d.updated_utc);
    $("#labStatus").innerHTML = '<span class="dot' + (live ? "" : " off") + '"></span>' + st;
    renderCards(); renderScore(); renderDaily(); renderForward(); renderHero(live); renderTape();
  }
  function renderCards() {
    $("#cards").innerHTML = rowsSorted().map(function (r) {
      var f = r.f[S.h]; if (!f) return "";
      var L = lean(f.p), tgt = f.px * (f.last ? r.px / f.last : 1);
      return '<button type="button" class="tcard" data-t="' + r.t + '" aria-label="' + esc(r.t + " forecast details") + '">' +
        '<div class="top"><span class="sym">' + r.t + '</span><span class="chg ' + cls(r.chg) + '">' + fmtPct(r.chg) + "</span></div>" +
        '<div class="nm">' + esc(r.name) + '</div><div class="px">' + fmtPx(r.px) + "</div>" +
        '<svg viewBox="0 0 240 64" preserveAspectRatio="none" aria-hidden="true">' + sparkSVG(r, f, 240, 64) + "</svg>" +
        '<div class="prob"><span>P(up)<span class="hzl"> · ' + HZ[S.h].toLowerCase() + '</span></span><b class="' + L[1] + '">' + pctTxt(f.p) + "</b></div>" + pbar(f.p) +
        '<div class="tgt"><span>' + L[0] + "</span><span>" + fmtPx(tgt) + " ±" + (f.sigma * 100).toFixed(2) + "%</span></div></button>";
    }).join("");
  }
  function renderScore() {
    var H = S.d.horizons, o = "";
    ["5m", "1h", "1d"].forEach(function (h) {
      var b = (H[h] || {}).backtest || {}; if (b.hit == null) return;
      var lo = 0.46, hi = 0.56, pos = function (v) { return Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)); };
      var sig = Math.abs(b.z) >= 2 ? (b.z > 0 ? "significant edge" : "significantly worse") : "not significant";
      o += '<div class="score-row"><span class="h">' + HZ[h] + '</span><span class="v ' + (b.hit > 0.5 ? "up" : "flat") + '">' + (b.hit * 100).toFixed(1) + "%</span>" +
        '<span class="s">z ' + (b.z > 0 ? "+" : "") + b.z.toFixed(1) + " · " + sig + " · n=" + b.n.toLocaleString("en-US") + "</span>" +
        '<div class="bar"><i style="width:' + pos(b.hit) + '%"></i><span class="mk half" style="left:' + pos(0.5) + '%"></span><span class="mk" style="left:' + pos(b.always_up) + '%" title="Always-up baseline ' + (b.always_up * 100).toFixed(1) + '%"></span></div></div>';
    });
    $("#scorecard").innerHTML = o;
  }
  function renderDaily() {
    var b = ((S.d.horizons["5m"] || {}).backtest || {}), D = (b.daily || []).slice(-20);
    if (!D.length) { $("#dailyChart").innerHTML = ""; return; }
    var w = 300, h = 110, bw = w / D.length, y = function (v) { return h / 2 - (v - 0.5) / 0.08 * (h / 2); };
    var s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" role="img" aria-label="Daily out-of-sample hit rate">';
    s += '<line x1="0" x2="' + w + '" y1="' + h / 2 + '" y2="' + h / 2 + '" stroke="var(--ink-4)" stroke-dasharray="3 4"/>';
    D.forEach(function (x, i) {
      var v = Math.max(0.42, Math.min(0.58, x[1])), y0 = h / 2, y1 = y(v);
      s += '<rect x="' + (i * bw + 2).toFixed(1) + '" y="' + Math.min(y0, y1).toFixed(1) + '" width="' + (bw - 4).toFixed(1) + '" height="' + Math.max(1, Math.abs(y1 - y0)).toFixed(1) + '" rx="1.5" fill="' + (x[1] >= 0.5 ? "var(--up)" : "var(--down)") + '"><title>' + x[0] + ": " + (x[1] * 100).toFixed(1) + "% of " + x[2] + "</title></rect>";
    });
    $("#dailyChart").innerHTML = s + "</svg>";
    var good = D.filter(function (x) { return x[1] > 0.5; }).length;
    $("#dailyNote").textContent = "5-minute horizon, last " + D.length + " trading days: above 50% on " + good + " of " + D.length + " days.";
  }
  function renderForward() {
    var L = S.d.live || {}, o = "";
    ["5m", "1h", "1d"].forEach(function (h) {
      var x = L[h] || {};
      o += '<div class="score-row"><span class="h">' + HZ[h] + '</span><span class="v">' + (x.hit != null ? (x.hit * 100).toFixed(1) + "%" : "—") + "</span>" +
        '<span class="s">' + (x.n ? x.n.toLocaleString("en-US") + " scored" + (x.since ? " since " + new Date(x.since).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "") : "Accumulating: scored after each bar closes") + "</span></div>";
    });
    $("#forward").innerHTML = o;
    var R = S.d.recent || [];
    $("#recent").innerHTML = R.length ? R.slice(0, 40).map(function (e) { return '<i class="' + (e.ok ? "ok" : "no") + '" title="' + esc(e.t + " " + e.h + " · " + (e.ok ? "correct" : "wrong")) + '"></i>'; }).join("") : "";
  }
  function renderHero(live) {
    var rs = rowsSorted(); if (!rs.length) return;
    var r = S.featured ? rs.filter(function (x) { return x.t === S.featured; })[0] || rs[0] : rs[0];
    S.featured = r.t;
    var f = r.f["5m"] || r.f[S.h];
    $("#liveDot").className = "dot" + (live ? "" : " off");
    $("#heroDot").className = "dot" + (live ? "" : " off");
    $("#liveLbl").textContent = live ? "Live forecast" : "Latest forecast";
    $("#liveAge").textContent = ago(S.d.updated_utc);
    $("#hlTk").textContent = r.t;
    $("#hlPx").innerHTML = fmtPx(r.px) + ' <span class="' + cls(r.chg) + '">' + fmtPct(r.chg) + "</span>";
    $("#hlSpark").innerHTML = sparkSVG(r, f, 300, 56, { proj: 0.18 });
    if (f) { var L = lean(f.p); $("#hlCall").innerHTML = "Next 5 min: <b class=\"" + L[1] + '">' + pctTxt(f.p) + "</b> chance of moving up · ±" + (f.sigma * 100).toFixed(2) + "% expected range"; }
  }
  function renderTape() {
    var items = rowsSorted().slice().sort(function (a, b) { return a.t < b.t ? -1 : 1; }).map(function (r) {
      var f = r.f["5m"]; var L = f ? lean(f.p) : ["", "flat"];
      return '<span class="tape-item"><b>' + r.t + '</b><span class="p">' + fmtPx(r.px) + '</span><span class="' + cls(r.chg) + '">' + fmtPct(r.chg) + "</span>" +
        (f ? '<span class="sig ' + L[1] + '">' + (f.p >= 0.5 ? "▲ " : "▼ ") + pctTxt(f.p) + "</span>" : "") + "</span>";
    }).join("");
    $("#tape").innerHTML = items + items;
  }

  /* ---------- ticker detail ---------- */
  function openTicker(t) {
    var r = Object.assign({ t: t }, S.d.rows[t]), H = S.d.horizons, dlg = $("#tickerDlg");
    var rows = ["5m", "1h", "1d"].map(function (h) {
      var f = r.f[h]; if (!f) return "";
      var k = f.last ? r.px / f.last : 1, b = (H[h] || {}).backtest || {}, L = lean(f.p);
      return "<tr><td>" + HZ[h] + '</td><td class="' + L[1] + '">' + pctTxt(f.p) + "</td><td>" + fmtPx(f.px * k) + "</td><td>" + fmtPx(f.lo * k) + " – " + fmtPx(f.hi * k) + "</td><td>" + (b.hit != null ? (b.hit * 100).toFixed(1) + "%" : "—") + "</td></tr>";
    }).join("");
    dlg.innerHTML = '<button class="x" type="button" aria-label="Close" data-close><svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" stroke-width="1.6"><path d="M2 2l10 10M12 2L2 12"/></svg></button>' +
      '<div class="cb"><div class="head"><b>' + t + '</b><span style="color:var(--ink-3)">' + esc(r.name) + '</span><span class="p">' + fmtPx(r.px) + '</span><span class="mono ' + cls(r.chg) + '">' + fmtPct(r.chg) + " today</span></div>" +
      '<div class="big-chart"><svg viewBox="0 0 800 240" preserveAspectRatio="none" aria-hidden="true">' + sparkSVG(r, r.f[S.h], 800, 240, { proj: 0.16, prev: true, sw: 2 }) + "</svg></div>" +
      '<div class="legend"><span><i style="background:var(--ink-2)"></i>Session price (5-min bars)</span><span><i style="background:var(--ink-4)"></i>Previous close</span><span><i style="background:var(--accent)"></i>' + HZ[S.h] + " forecast ±1σ</span></div>" +
      '<div class="tbl-scroll"><table class="hz-table"><thead><tr><th>Horizon</th><th>P(up)</th><th>Expected</th><th>68% range</th><th>Model hit rate*</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
      '<p class="disclaimer" style="margin-top:14px">*Walk-forward, pooled across all 16 stocks. Each horizon applies the model’s expected return from its last completed bar to the current price. Not investment advice.</p></div>';
    dlg.showModal();
  }
  $("#cards").addEventListener("click", function (e) { var b = e.target.closest("[data-t]"); if (b) openTicker(b.getAttribute("data-t")); });
  $("#hzSeg").addEventListener("click", function (e) {
    var b = e.target.closest("button"); if (!b || !S.d) return;
    S.h = b.getAttribute("data-h");
    this.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
    renderCards();
  });

  window.labSummary = function () {
    if (!S.d) return null;
    var H = S.d.horizons, b5 = H["5m"].backtest, b1 = H["1h"].backtest, bd = H["1d"].backtest;
    return "Latest walk-forward results: <b>" + (b5.hit * 100).toFixed(1) + "%</b> at 5 minutes (z = " + b5.z.toFixed(1) + ", n = " + b5.n.toLocaleString("en-US") + "), " +
      (b1.hit * 100).toFixed(1) + "% hourly (z = " + b1.z.toFixed(1) + ") and " + (bd.hit * 100).toFixed(1) + "% per session (z = " + bd.z.toFixed(1) + "). Only the shortest horizon shows a statistically significant edge.";
  };

  load();
  setInterval(function () { if (!document.hidden) load(); }, POLL_MS);
  setInterval(function () { if (S.d && !document.hidden) { var rs = rowsSorted().slice(0, 6); if (rs.length) { var i = rs.map(function (x) { return x.t; }).indexOf(S.featured); S.featured = rs[(i + 1) % rs.length].t; renderHero(!S.snapshot && S.d.market.open); } } }, 7000);
})();
