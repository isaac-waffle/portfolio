/* Site chrome, hero field, projects + case studies. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("#yr").textContent = new Date().getFullYear();

  /* ---------- nav ---------- */
  var nav = $("#nav"), btn = $("#menuBtn");
  var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 12); };
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();
  btn.addEventListener("click", function () { var o = nav.classList.toggle("open"); btn.setAttribute("aria-expanded", o); });
  $("#menu").addEventListener("click", function (e) { if (e.target.closest("a")) { nav.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); } });

  /* ---------- reveal ---------- */
  window.observeReveal = function (root) {
    var els = (root || document).querySelectorAll(".reveal:not(.in)");
    if (!("IntersectionObserver" in window) || reduce) { els.forEach(function (e) { e.classList.add("in"); }); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
    els.forEach(function (e, i) { e.style.transitionDelay = Math.min(i % 6, 5) * 60 + "ms"; io.observe(e); });
  };

  /* ---------- hero: drifting random-walk field ---------- */
  (function field() {
    var c = $("#field"); if (!c || !c.getContext) return;
    var ctx = c.getContext("2d"), W, H, paths = [], dpr = Math.min(window.devicePixelRatio || 1, 2), t0 = performance.now();
    function seed() {
      W = c.clientWidth; H = c.clientHeight; c.width = W * dpr; c.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      paths = [];
      var n = W < 700 ? 14 : 26;
      for (var i = 0; i < n; i++) {
        var pts = [], y = H * (0.15 + 0.7 * Math.random()), v = 0;
        for (var x = 0; x <= W + 20; x += 6) { v = v * 0.86 + (Math.random() - 0.5) * 3.2; y += v; pts.push(y); }
        paths.push({ pts: pts, off: Math.random() * 1000, hue: Math.random() < 0.18, a: 0.08 + Math.random() * 0.16 });
      }
    }
    function draw(now) {
      var t = (now - t0) / 1000;
      ctx.clearRect(0, 0, W, H);
      paths.forEach(function (p, k) {
        ctx.beginPath();
        var shift = Math.sin(t * 0.15 + p.off) * 18;
        for (var i = 0; i < p.pts.length; i++) {
          var x = i * 6, y = p.pts[i] + shift + Math.sin(t * 0.4 + i * 0.05 + k) * 3;
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.strokeStyle = p.hue ? "rgba(198,243,107," + (p.a + 0.08) + ")" : "rgba(236,238,241," + p.a + ")";
        ctx.lineWidth = p.hue ? 1.2 : 0.8; ctx.stroke();
      });
      if (!reduce) raf = requestAnimationFrame(draw);
    }
    var raf; seed(); draw(t0);
    var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { seed(); if (reduce) draw(performance.now()); }, 200); });
    document.addEventListener("visibilitychange", function () { if (document.hidden) cancelAnimationFrame(raf); else if (!reduce) raf = requestAnimationFrame(draw); });
  })();

  /* ---------- seeded helpers for project art ---------- */
  function rng(s) { return function () { s = (s * 1664525 + 1013904223) % 4294967296; return s / 4294967296; }; }
  var C = { ink: "#1d1d1f", ink2: "#424245", ink3: "#86868b", ink4: "#d2d2d7", line: "#f0f0f3", line2: "#e3e3e8", acc: "#1d1d1f", up: "#34c759", dn: "#ff3b30", blue: "#1d1d1f", bg: "#f5f5f7", panel: "#ffffff" };
  function grid() { return ""; }
  function walk(r, n, y0, vol) { var y = y0, out = []; for (var i = 0; i < n; i++) { y += (r() - 0.5) * vol; out.push(y); } return out; }
  function poly(xs, ys) { return xs.map(function (x, i) { return (i ? "L" : "M") + x.toFixed(1) + "," + ys[i].toFixed(1); }).join(""); }

  var ART = {
    lab: function () {
      var w = 1050, h = 400, r = rng(7), n = 100, ys = walk(r, n, 230, 14), xs = ys.map(function (_, i) { return 40 + i * 6.2; });
      var lx = xs[n - 1], ly = ys[n - 1], s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice">' + grid(w, h, 50);
      s += '<defs><linearGradient id="lg1" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="' + C.acc + '" stop-opacity=".25"/><stop offset="1" stop-color="' + C.acc + '" stop-opacity="0"/></linearGradient></defs>';
      s += '<path d="' + poly(xs, ys) + "L" + lx + "," + h + "L40," + h + 'Z" fill="url(#lg1)" opacity=".5"/>';
      s += '<path d="' + poly(xs, ys) + '" fill="none" stroke="' + C.ink + '" stroke-width="2"/>';
      [[90, 34, .28], [180, 62, .18], [290, 96, .1]].forEach(function (c) {
        s += '<path d="M' + lx + "," + ly + "L" + (lx + c[0]) + "," + (ly - c[1] - 10) + "L" + (lx + c[0]) + "," + (ly + c[1] - 10) + 'Z" fill="' + C.acc + '" opacity="' + c[2] + '"/>';
      });
      s += '<line x1="' + lx + '" y1="' + ly + '" x2="' + (lx + 290) + '" y2="' + (ly - 22) + '" stroke="' + C.acc + '" stroke-width="2" stroke-dasharray="6 6"/>';
      s += '<circle cx="' + lx + '" cy="' + ly + '" r="6" fill="' + C.acc + '"/><circle cx="' + lx + '" cy="' + ly + '" r="14" fill="none" stroke="' + C.acc + '" opacity=".4"/>';
      ["5 MIN", "1 HOUR", "SESSION"].forEach(function (t, i) { var x = lx + [90, 180, 290][i]; s += '<text x="' + x + '" y="' + (h - 24) + '" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="12" text-anchor="middle">' + t + '</text><line x1="' + x + '" x2="' + x + '" y1="40" y2="' + (h - 44) + '" stroke="' + C.line2 + '" stroke-dasharray="3 5"/>'; });
      s += '<g font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="13"><text x="' + (lx + 18) + '" y="' + (ly - 64) + '" fill="' + C.acc + '">P(up) 0.531</text><text x="' + (lx + 18) + '" y="' + (ly - 44) + '" fill="' + C.ink3 + '">±1σ range</text></g>';
      return s + "</svg>";
    },
    study: function () {
      var w = 640, h = 360, s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice">' + grid(w, h, 40);
      var hz = [["1m", 51.5, 49.6], ["5m", 51.2, 49.4], ["30m", 52.4, 49.5], ["1h", 49.5, 50.7], ["1d", 52.2, 48.9]];
      var y = function (v) { return 300 - (v - 46) * 30; };
      s += '<line x1="40" x2="600" y1="' + y(50) + '" y2="' + y(50) + '" stroke="' + C.ink3 + '" stroke-dasharray="4 5"/><text x="604" y="' + (y(50) + 4) + '" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11">50%</text>';
      hz.forEach(function (d, i) {
        var x = 80 + i * 105;
        s += '<rect x="' + x + '" y="' + y(d[1]) + '" width="30" height="' + (300 - y(d[1])) + '" rx="3" fill="' + C.acc + '"/>';
        s += '<rect x="' + (x + 36) + '" y="' + y(d[2]) + '" width="30" height="' + (300 - y(d[2])) + '" rx="3" fill="' + C.ink4 + '"/>';
        s += '<text x="' + (x + 33) + '" y="326" fill="' + C.ink2 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="12" text-anchor="middle">' + d[0] + "</text>";
      });
      s += '<g font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11"><rect x="420" y="40" width="10" height="10" fill="' + C.acc + '"/><text x="436" y="49" fill="' + C.ink2 + '">MACHINE</text><rect x="500" y="40" width="10" height="10" fill="' + C.ink4 + '"/><text x="516" y="49" fill="' + C.ink2 + '">HUMAN RULE</text></g>';
      return s + "</svg>";
    },
    desk: function () {
      var w = 640, h = 360, r = rng(19), s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>';
      var cols = ["1m", "5m", "30m", "1h", "1d"], tks = ["NVDA", "AMD", "TSLA", "META", "MU", "ORCL", "GOOGL"];
      s += '<g font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11">';
      cols.forEach(function (c, j) { s += '<text x="' + (190 + j * 88) + '" y="52" fill="' + C.ink3 + '" text-anchor="middle">' + c + "</text>"; });
      tks.forEach(function (t, i) {
        var yy = 84 + i * 38;
        s += '<text x="60" y="' + (yy + 4) + '" fill="' + C.ink + '">' + t + "</text>";
        cols.forEach(function (_, j) {
          var v = (r() - 0.5) * 2, col = v > 0 ? C.up : C.dn, a = 0.15 + Math.abs(v) * 0.55;
          s += '<rect x="' + (156 + j * 88) + '" y="' + (yy - 12) + '" width="68" height="24" rx="5" fill="' + col + '" opacity="' + a.toFixed(2) + '"/>';
          s += '<text x="' + (190 + j * 88) + '" y="' + (yy + 4) + '" fill="' + C.ink + '" text-anchor="middle">' + (v > 0 ? "+" : "−") + Math.abs(v * 0.4).toFixed(2) + "%</text>";
        });
      });
      return s + "</g></svg>";
    },
    radar: function () {
      var w = 1050, h = 400, s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>';
      var jobs = [["Data Analyst", "Fintech · Amsterdam", 88], ["Junior Data Scientist", "Retail · Utrecht", 81], ["BI Developer", "Energy · Rotterdam", 74], ["Analytics Engineer", "Mobility · Amsterdam", 66], ["Quant Analyst", "Trading · Amsterdam", 59]];
      jobs.forEach(function (j, i) {
        var y = 54 + i * 66, col = j[2] >= 75 ? C.up : j[2] >= 60 ? C.acc : "#f0b060", circ = 2 * Math.PI * 20;
        s += '<rect x="60" y="' + y + '" width="430" height="54" rx="10" fill="' + C.panel + '" stroke="' + (i ? C.line : C.acc) + '"/>';
        s += '<circle cx="92" cy="' + (y + 27) + '" r="20" fill="none" stroke="' + C.line2 + '" stroke-width="4"/><circle cx="92" cy="' + (y + 27) + '" r="20" fill="none" stroke="' + col + '" stroke-width="4" stroke-dasharray="' + (circ * j[2] / 100).toFixed(1) + " " + circ.toFixed(1) + '" transform="rotate(-90 92 ' + (y + 27) + ')"/>';
        s += '<text x="92" y="' + (y + 31) + '" fill="' + C.ink + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="12" text-anchor="middle">' + j[2] + "</text>";
        s += '<text x="126" y="' + (y + 23) + '" fill="' + C.ink + '" font-family="-apple-system,Inter,sans-serif" font-size="14" font-weight="600">' + j[0] + '</text><text x="126" y="' + (y + 41) + '" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-size="12">' + j[1] + "</text>";
        s += '<rect x="390" y="' + (y + 17) + '" width="84" height="20" rx="10" fill="' + C.up + '" opacity=".14"/><text x="432" y="' + (y + 31) + '" fill="' + C.up + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="10" text-anchor="middle">IND SPONSOR</text>';
      });
      s += '<rect x="540" y="54" width="450" height="318" rx="12" fill="' + C.panel + '" stroke="' + C.line + '"/>';
      s += '<text x="566" y="88" fill="' + C.acc + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11" letter-spacing="1.5">TAILORED MOTIVATION LETTER</text>';
      [0.92, 0.86, 0.95, 0.6, 0, 0.9, 0.82, 0.94, 0.88, 0.5, 0, 0.93, 0.7].forEach(function (f, i) { if (f) s += '<rect x="566" y="' + (110 + i * 18) + '" width="' + (398 * f) + '" height="7" rx="3.5" fill="' + C.ink4 + '"/>'; });
      s += '<rect x="566" y="338" width="110" height="22" rx="11" fill="' + C.acc + '"/><text x="621" y="353" fill="#ffffff" font-family="-apple-system,Inter,sans-serif" font-size="11" font-weight="600" text-anchor="middle">Save as PDF</text>';
      return s + "</svg>";
    },
    earn: function () {
      var w = 640, h = 360, r = rng(7), s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>';
      var days = ["MON", "TUE", "WED", "THU", "FRI"];
      for (var d = 0; d < 5; d++) {
        var x = 60 + d * 108;
        s += '<text x="' + x + '" y="58" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11">' + days[d] + "</text>";
        for (var k = 0; k < 4; k++) { var on = r() > 0.3; s += '<rect x="' + x + '" y="' + (72 + k * 30) + '" width="' + (52 + r() * 40).toFixed(0) + '" height="20" rx="5" fill="' + (on && d === 2 && k === 0 ? C.acc : C.panel) + '" stroke="' + C.line2 + '"/>'; }
      }
      for (var i = 0; i < 20; i++) { var v = (r() - 0.45) * 70, bx = 60 + i * 26; s += '<rect x="' + bx + '" y="' + (v > 0 ? 300 - v : 300) + '" width="16" height="' + Math.abs(v).toFixed(1) + '" rx="3" fill="' + (v > 0 ? C.up : C.dn) + '"/>'; }
      s += '<line x1="56" x2="590" y1="300" y2="300" stroke="' + C.line2 + '"/>';
      return s + "</svg>";
    },
    pnl: function () {
      var w = 640, h = 360, r = rng(42), s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>';
      s += '<text x="60" y="64" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-size="12">Realised P&amp;L</text><text x="60" y="104" fill="' + C.up + '" font-family="-apple-system,Inter,sans-serif" font-size="38" font-weight="700" letter-spacing="-1">+€2,418</text>';
      for (var k = 0; k < 5; k++) for (var d = 0; d < 7; d++) {
        var x = 300 + d * 40, y = 44 + k * 40, v = r(), on = d < 5 && r() > 0.35, up = r() > 0.42;
        s += '<rect x="' + x + '" y="' + y + '" width="34" height="34" rx="7" fill="' + (on ? (up ? C.up : C.dn) : C.panel) + '" opacity="' + (on ? (0.25 + v * 0.6).toFixed(2) : 1) + '"/>';
      }
      for (var i = 0; i < 24; i++) { var v2 = (r() - 0.4) * 60, bx = 60 + i * 22; s += '<rect x="' + bx + '" y="' + (v2 > 0 ? 300 - v2 : 300) + '" width="14" height="' + Math.abs(v2) + '" rx="3" fill="' + (v2 > 0 ? C.up : C.dn) + '"/>'; }
      s += '<line x1="56" x2="590" y1="300" y2="300" stroke="' + C.line2 + '"/>';
      return s + "</svg>";
    },
    housing: function () {
      var OUT = [[4.75, 52.96], [5.05, 53.05], [5.42, 53.18], [5.9, 53.3], [6.2, 53.42], [6.85, 53.35], [7.2, 53.25], [7.2, 53.0], [7.05, 52.65], [7.07, 52.25], [6.85, 51.97], [6.4, 51.85], [6.05, 51.85], [6.2, 51.4], [6.05, 51.15], [5.85, 50.8], [5.7, 50.75], [5.75, 51.2], [5.5, 51.3], [5.0, 51.45], [4.5, 51.4], [4.25, 51.37], [3.6, 51.3], [3.4, 51.4], [3.65, 51.6], [4.0, 51.85], [4.1, 51.98], [4.27, 52.1], [4.6, 52.46], [4.65, 52.8]];
      var px = function (l) { return 60 + (l - 3.2) * 56; }, py = function (l) { return 20 + (53.7 - l) * 105; };
      var w = 640, h = 360, s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>' + grid(w, h, 40);
      s += '<polygon points="' + OUT.map(function (q) { return px(q[0]).toFixed(1) + "," + py(q[1]).toFixed(1); }).join(" ") + '" fill="' + C.panel + '" stroke="' + C.ink3 + '" stroke-width="1.2"/>';
      [[4.9, 52.37, 1], [4.48, 51.92, 1], [5.12, 52.09, 0], [4.3, 52.08, 0], [5.47, 51.44, 0], [6.57, 53.22, 0], [4.63, 52.38, 0]].forEach(function (c) {
        s += '<circle cx="' + px(c[0]) + '" cy="' + py(c[1]) + '" r="' + (c[2] ? 7 : 5) + '" fill="' + (c[2] ? C.acc : C.bg) + '" stroke="' + (c[2] ? C.acc : C.ink2) + '" stroke-width="1.5"/>';
      });
      s += '<g transform="translate(372 70)"><rect width="220" height="220" rx="14" fill="' + C.panel + '" stroke="' + C.line2 + '"/>';
      s += '<path d="M110 34 L160 54 V100 C160 132 138 154 110 166 C82 154 60 132 60 100 V54 Z" fill="none" stroke="' + C.acc + '" stroke-width="2.5"/><path d="M90 100 l14 14 l26 -28" fill="none" stroke="' + C.acc + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
      s += '<text x="110" y="196" fill="' + C.ink2 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11" text-anchor="middle">SCAM SIGNALS: 0</text></g>';
      return s + "</svg>";
    },
    thesis: function () {
      var w = 640, h = 360, s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>' + grid(w, h, 40);
      var xs = [], ale = [], lin = [];
      for (var i = 0; i <= 50; i++) { var x = 70 + i * 10.4, u = i / 50; xs.push(x); ale.push(270 - 160 * (1 / (1 + Math.exp(-(u - 0.5) * 9))) - 12 * Math.sin(u * 9)); lin.push(262 - 150 * u); }
      s += '<path d="' + poly(xs, lin) + '" fill="none" stroke="' + C.ink3 + '" stroke-width="2" stroke-dasharray="7 6"/>';
      s += '<path d="' + poly(xs, ale) + '" fill="none" stroke="' + C.acc + '" stroke-width="3"/>';
      for (var j = 0; j < 40; j++) { var xx = 70 + j * 13, yy = 300 + ((j * 37) % 11); s += '<line x1="' + xx + '" x2="' + xx + '" y1="' + yy + '" y2="' + (yy + 8) + '" stroke="' + C.ink4 + '"/>'; }
      s += '<g font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11"><line x1="70" x2="96" y1="52" y2="52" stroke="' + C.acc + '" stroke-width="3"/><text x="104" y="56" fill="' + C.ink2 + '">ALE (black-box model)</text><line x1="300" x2="326" y1="52" y2="52" stroke="' + C.ink3 + '" stroke-width="2" stroke-dasharray="6 5"/><text x="334" y="56" fill="' + C.ink2 + '">Econometric partial effect</text></g>';
      return s + "</svg>";
    },
    waze: function () {
      var w = 640, h = 360, s = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid slice"><rect width="' + w + '" height="' + h + '" fill="' + C.bg + '"/>';
      function tree(cx, top, sc, col) {
        var o = "", nodes = [[0, 0], [-1, 1], [1, 1], [-1.5, 2], [-0.5, 2], [0.5, 2], [1.5, 2]];
        [[0, 1], [0, 2], [1, 3], [1, 4], [2, 5], [2, 6]].forEach(function (e) { var a = nodes[e[0]], b = nodes[e[1]]; o += '<line x1="' + (cx + a[0] * 40 * sc) + '" y1="' + (top + a[1] * 64 * sc) + '" x2="' + (cx + b[0] * 40 * sc) + '" y2="' + (top + b[1] * 64 * sc) + '" stroke="' + C.line2 + '" stroke-width="1.5"/>'; });
        nodes.forEach(function (n, i) { var leaf = i > 2; o += '<circle cx="' + (cx + n[0] * 40 * sc) + '" cy="' + (top + n[1] * 64 * sc) + '" r="' + (9 * sc) + '" fill="' + (leaf ? ((i % 2) ? col : C.ink4) : C.panel) + '" stroke="' + (leaf ? "none" : C.ink2) + '" stroke-width="1.5"/>'; });
        return o;
      }
      s += tree(150, 90, 1, C.dn) + tree(320, 70, 1.15, C.acc) + tree(490, 90, 1, C.dn);
      s += '<text x="320" y="320" fill="' + C.ink3 + '" font-family="-apple-system,Inter,sans-serif" font-weight="600" font-size="11" text-anchor="middle">DECISION TREE · RANDOM FOREST · XGBOOST</text>';
      return s + "</svg>";
    }
  };

  /* ---------- project content ---------- */
  var P = [
    { id: "lab", art: "lab", chip: "Live", live: true, meta: "2026 · Forecasting · MLOps",
      title: "Live Stock Forecast Lab",
      blurb: "A scheduled cloud pipeline that publishes next-5-minute, next-hour and next-session forecasts for 16 stocks, then grades every forecast against what the market actually did.",
      tags: ["Python", "scikit-learn", "Walk-forward", "GitHub Actions", "Static JSON API"],
      sum: "Most stock “predictions” online are never scored. This one runs unattended in the cloud, validates without look-ahead, and keeps a public forward-test record.",
      kpis: [["16", "stocks"], ["3", "horizons"], ["15 min", "refresh"], ["100%", "forecasts scored"]],
      cols: [
        ["Approach", ["<b>Data:</b> 5-minute, hourly and daily bars for 16 large caps plus SPY/QQQ; still-forming bars are dropped.",
          "<b>Features:</b> volatility-scaled returns (lags 1–5), 5/20-bar momentum, volatility ratio, distance to mean, range, volume shocks, market moves, peer spillover, time of day.",
          "<b>Model:</b> pooled ridge + L2 logistic ensemble → P(up), expected move and ±1σ band.",
          "<b>Validation:</b> rolling walk-forward retraining scored only on unseen bars, with z-scores against a coin flip and an always-up baseline."]],
        ["Engineering", ["<b>Serverless:</b> a GitHub Actions cron job runs every 15 minutes in US market hours; no servers to maintain.",
          "<b>Data API:</b> results are committed as JSON to a data branch and fetched by this static site over the GitHub CDN.",
          "<b>Accountability:</b> every forecast is appended to a log and automatically scored once its bar closes.",
          "<b>Cost-aware:</b> the walk-forward backtest is cached and recomputed once per day."]],
        ["Results", ["<span data-lab-summary>The 5-minute horizon shows a small but statistically significant directional edge; hourly and daily horizons are indistinguishable from a coin flip.</span>",
          "That pattern matches weak-form market efficiency, and the edge is smaller than realistic trading costs. The lab reports it honestly instead of hiding it."]]
      ],
      links: [["Open the live lab", "lab.html", true], ["View the code", "https://github.com/isaac-waffle/portfolio/tree/main/forecast"]] },

    { id: "study", art: "study", chip: "Research", meta: "2026 · Quant research · 15-year backtest",
      title: "Machine vs. Human: Can ML Out-Forecast Trading Rules?",
      blurb: "A look-ahead-free test of 11 models against the rules of thumb traders use, across 16 stocks and five horizons, from one minute to one day, over 15 years.",
      tags: ["LightGBM", "XGBoost", "LSTM", "ARIMA", "Multiple testing", "HAC"],
      sum: "A fair fight between machine-learned forecasts and the heuristics people actually trade on, judged by hit rate, statistical significance and, the part that matters, profit after costs.",
      kpis: [["43,345", "daily forecasts"], ["3,706", "trading days"], ["11", "models"], ["40", "tests audited"]],
      cols: [
        ["Design", ["<b>Universe:</b> 16 tech, semiconductor and growth names; cross-stock spillover features weighted by 60-day correlation (a graph message-passing signal).",
          "<b>Contenders:</b> LightGBM (own vs. cross-stock), XGBoost, random forest, logistic regression, LSTM, ARIMA, ridge/VAR and a median ensemble vs. a 5/20 moving-average rule, momentum and always-up.",
          "<b>Protocol:</b> rolling walk-forward (250-day training, retrain every 5 days); intraday targets never cross the overnight gap."]],
        ["Robustness", ["Day-clustered p-values, so correlated stocks on one day don't inflate significance.",
          "Holm and Benjamini–Hochberg corrections across 40 model × horizon tests.",
          "Cost stress at 1×, 1.5× and 2×; residual-alpha regressions vs. buy-and-hold, SPY and QQQ with HAC t-stats; regime splits via IBD distribution days."]],
        ["Findings", ["Machines beat the human rules at every horizon except one hour: <b>~51–53% vs. ~49–50%</b>.",
          "Over 2012–2026, logistic regression reached <b>51.8%</b> and boosted trees ~51.0%, still significant after correction.",
          "<b>But no model beat buy-and-hold after costs</b>, and alpha against holding was negative everywhere. Models only helped by sidestepping drawdowns (2022: −22% to −31% vs. −36%).",
          "Intraday: 0 of 40 combinations met the deploy bar; at 1–5 minutes, costs were 6–13× the gross edge."]],
        ["Takeaway", ["Statistical significance is not economic significance. I also documented survivorship bias in the universe and why it doesn't change the conclusion."]]
      ],
      links: [] },

    { id: "desk", art: "desk", chip: "Real-time", meta: "2026 · Streaming · 8 models × 5 horizons",
      title: "Real-Time Intraday Forecast Desk",
      blurb: "The live counterpart of the study: predicts the next 1m / 5m / 30m / 1h / 1d bar with eight models, streams quotes every second, and scores itself as it goes.",
      tags: ["PyTorch", "LightGBM", "GARCH", "Order flow", "PWA"],
      sum: "A local trading-desk system for testing ideas live before believing them. It is analysis only and never places orders.",
      kpis: [["8", "models per horizon"], ["5", "horizons"], ["1 s", "quote stream"], ["400", "bars for range calibration"]],
      cols: [
        ["System", ["An event loop wakes every minute, keeps only completed bars and re-forecasts each horizon on its bar boundary.",
          "Retraining schedule by horizon (1m hourly, 5m every 2h, 30m/1h/1d daily); models reused in between.",
          "A one-second quote stream with tick-rule order-flow imbalance, an options-chain logger (put/call, OI, ATM IV, skew) every 15 minutes, and GDELT news-tone signals."]],
        ["Modelling", ["Ridge, LightGBM (own and cross-stock), XGBoost, random forest, logistic, LSTM and ARIMA.",
          "GARCH(1,1) and time-of-day volatility profiles for ranges, plus a :00/:30 clock effect.",
          "Ranges self-calibrate on the last 400 scored predictions so the 68% band actually covers ~68%."]],
        ["Validated before shipping", ["Time-of-day features lifted 5-minute range coverage in the first 30 minutes from <b>36% to 67%</b> and direction from 52.2% to 53.2%.",
          "Every addition was tested on held-out bars first. Several (futures, VIX, news-tone) were rejected for showing no gain."]],
        ["Interface", ["A watchlist, model-by-model forecast matrix, candlestick charts, live scoreboard, market-regime monitor (IBD distribution days) and a phone-friendly installable web app."]]
      ],
      links: [] },

    { id: "radar", art: "radar", chip: "AI tool", meta: "2026 · LLM orchestration · Two iterations",
      title: "NL Data Job Radar",
      blurb: "An AI-assisted job-search pipeline for the Dutch market: it finds data roles, filters them against visa-sponsorship and salary rules, scores the fit, and drafts a tailored CV and motivation letter for each match.",
      tags: ["JavaScript", "LLM (Claude)", "LaTeX", "PDF engine", "Scheduled agents", "Document DB"],
      sum: "Job hunting as a non-EU graduate in the Netherlands means one question first: can this employer sponsor me? I automated the filtering, scoring and writing, so my time goes into the applications that can actually work.",
      kpis: [["0–100", "fit score per role"], ["2", "hard visa/salary filters"], ["1 page", "auto-fit CV"], ["v1 → v2", "iterations"]],
      cols: [
        ["Pipeline", ["<b>Hard filters:</b> the employer must be on the IND recognised-sponsor register and the salary must meet the 2026 skilled-migrant threshold; Dutch-language requirements are flagged.",
          "<b>Fit scoring:</b> each posting is scored 0–100 against my profile, with explicit strengths and gaps plus how to close them.",
          "<b>Tracker:</b> a status pipeline (new → applied → interview → offer) with notes, on a shared database."]],
        ["Generation", ["Paste any job description and the LLM writes a tailored LaTeX (moderncv) CV and a 250–350-word letter. The prompt rules forbid inventing experience, numbers or tools.",
          "I wrote a <b>dependency-free PDF writer in JavaScript</b> (font metrics, word-wrap, auto-shrink to one page) so documents stay selectable and ATS-readable.",
          "A one-click refresh triggers a scheduled cloud agent that re-verifies every posting and searches for new ones."]],
        ["v2", ["Rebuilt the scoring around my updated profile and regenerated every CV and letter, so the tool improved as my experience grew."]],
        ["Note", ["Contains personal documents, so there is no public demo. Happy to walk through it live."]]
      ],
      links: [] },

    { id: "earnings", art: "earn", chip: "Live demo", live: true, meta: "2026 · Equity research · Scheduled data API",
      title: "Earnings Desk",
      blurb: "Every upcoming US earnings report for a 50-stock watchlist and the whole market, with a full pre-earnings brief per company in English or Korean.",
      tags: ["Python", "yfinance", "Options data", "GitHub Actions", "SVG charts", "i18n"],
      sum: "Before an earnings report I want the same eight answers every time: what the company does, where the stock stands, how it reacted before, and how big a move the market expects. This puts them on one page.",
      kpis: [["1,000+", "reports on the calendar"], ["50", "watchlist names"], ["8", "sections per company"], ["2", "languages"]],
      cols: [
        ["Pipeline", ["<b>Calendar:</b> the Nasdaq earnings calendar for the next two weeks, every US listing, with timing, consensus EPS and market cap.",
          "<b>Detail files:</b> every watchlist name plus each $50B+ company on the two-week calendar gets its own JSON brief.",
          "<b>Serverless:</b> a GitHub Actions job rebuilds the feed every three hours and force-pushes it to a data branch; a ticker that fails keeps its last good file."]],
        ["Analysis", ["<b>Past reactions:</b> gap, reaction-day and 5-day moves for up to 20 reports, timed correctly for pre-market vs after-close releases.",
          "<b>Expected move:</b> the at-the-money straddle on the first expiry after the report, shown as a ±1σ/±2σ price range next to the stock's own history.",
          "<b>Context:</b> trend vs 50/200-day averages, RSI, 52-week position, valuation, balance sheet, short interest, analyst targets and news."]],
        ["Experience", ["Double-click any calendar row to open the brief; English by default with a one-click Korean version, including hand-written Korean company profiles.",
          "Hand-rolled SVG charts that redraw to the screen width, in light and dark mode."]]
      ],
      links: [["Try the live demo", "demos/earnings.html", true], ["View the code", "https://github.com/isaac-waffle/portfolio/tree/main/earnings"]] },

    { id: "pnl", art: "pnl", chip: "Live demo", live: true, meta: "2026 · Finance tooling · Client-side",
      title: "DEGIRO P&L Analyzer",
      blurb: "Drop in a brokerage transaction export and get realised profit and loss by day, week, month and stock, computed entirely in your browser.",
      tags: ["JavaScript", "FIFO accounting", "SVG charts", "CSV parsing", "Privacy by design"],
      sum: "My broker shows positions, not a clear history of what I actually earned. This tool turns the raw export into the view I wanted, without the data ever leaving the device.",
      kpis: [["FIFO", "lot matching"], ["4", "time views"], ["0", "bytes uploaded"]],
      cols: [
        ["Engineering", ["Robust CSV ingestion: auto-detects the delimiter, English or Dutch headers, and European vs. US number formats.",
          "FIFO lot matching for realised P&L including fees; sells without matching buys are flagged rather than silently mis-counted.",
          "No dependencies: the charts are hand-rolled SVG and the CSV parser is quote-aware."]],
        ["Experience", ["Calendar heatmap with weekly totals; day, week and month bar charts; per-stock attribution on hover.",
          "Sortable per-stock returns and open positions with mark-to-market from user-entered prices.",
          "Switchable gain colours (green/red Western or red/blue Korean convention)."]]
      ],
      links: [["Try the live demo", "demos/pnl.html", true]] },

    { id: "housing", art: "housing", chip: "Live demo", live: true, meta: "2026 · Trust & safety · Rule engine + LLM",
      title: "Woning Scout: Safe Housing Search NL",
      blurb: "Search only trusted Dutch housing platforms, and screen any listing or landlord message for the patterns behind rental scams.",
      tags: ["JavaScript", "Rule engine", "Regex NLP", "SVG map", "LLM second opinion"],
      sum: "Rental scams are a known hazard of moving to Amsterdam. I turned the warning signs into a filter that anyone can run before sending a deposit.",
      kpis: [["8", "scam-signal families"], ["14", "cities"], ["5", "trusted platforms"]],
      cols: [
        ["Search", ["Rent or buy mode, a dual-handle price slider, and 14 cities via chips or an interactive map.",
          "Builds deep links with the filters pre-applied for Pararius, Funda, Huurwoningen, Kamernet and Jaap."]],
        ["Scam screening", ["<b>Strict allow-list</b> of trusted domains; known-risk and unknown domains are rejected, and punycode look-alike domains are detected.",
          "<b>Weighted rule engine</b> over eight signal families in English and Dutch: upfront deposits, landlord abroad, keys by mail, off-platform messaging, untraceable payments, pressure tactics, free-mail contacts and early ID requests.",
          "Optional LLM second opinion that returns a structured risk rating and next step (in the original app)."]]
      ],
      links: [["Try the live demo", "demos/housing.html", true]] },

    { id: "thesis", art: "thesis", chip: "Publication", meta: "2024 · BSc thesis · Cum laude",
      title: "Explainable AI vs. Econometric Partial Effects",
      blurb: "Do model-agnostic explanation methods recover what econometrics already knows? A comparison of Accumulated Local Effects from ML models with classical partial effects.",
      tags: ["Econometrics", "Explainable AI", "ALE", "Machine learning", "R / Python"],
      sum: "Black-box models predict well but say little. Econometric models explain but can be rigid. My thesis asks whether XAI can put the two on common ground.",
      cols: [
        ["What I did", ["Fitted classical econometric specifications and flexible machine-learning models on the same data.",
          "Extracted feature effects from the black-box models with Accumulated Local Effects (ALE), which stays robust when features are correlated.",
          "Compared those effects with econometric partial effects in size, shape and direction."]],
        ["Conclusion", ["XAI makes black-box behaviour interpretable in econometric terms, and it exposes non-linearities a linear specification would miss.",
          "Published in the University of Amsterdam thesis repository."]]
      ],
      links: [["Read the thesis", "https://scripties.uba.uva.nl/search?id=c11249314", false, true]] },

    { id: "waze", art: "waze", chip: "Capstone", meta: "2025 · Google Advanced Data Analytics",
      title: "Waze User Churn Classification",
      blurb: "End-to-end churn modelling for a navigation app: EDA, hypothesis testing, then decision tree, random forest and XGBoost classifiers.",
      tags: ["Python", "pandas", "scikit-learn", "XGBoost", "Hypothesis testing"],
      sum: "Capstone for the Google Advanced Data Analytics certificate: identify which users are likely to churn so the product team can intervene.",
      cols: [
        ["Approach", ["Exploratory analysis and statistical testing of usage behaviour between churned and retained users.",
          "Feature engineering on driving and engagement activity.",
          "Trained and compared a decision tree, random forest and XGBoost, tuned with cross-validation and evaluated on held-out data."]],
        ["Output", ["Model comparison and feature importance, translated into retention recommendations for a non-technical audience."]]
      ],
      links: [] }
  ];

  var pc = document.getElementById("projCount"); if (pc) pc.textContent = P.length;
  var arrow = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8h10M9 4l4 4-4 4"/></svg>';
  var grid_ = $("#workGrid");
  grid_.innerHTML = P.map(function (p) {
    return '<button type="button" class="proj reveal ' + (p.size || "") + '" data-p="' + p.id + '" aria-haspopup="dialog">' +
      '<div class="vis"><span class="chip' + (p.live ? " live" : "") + '">' + (p.live ? '<span class="dot"></span>' : "") + esc(p.chip) + "</span>" + ART[p.art]() + "</div>" +
      '<div class="body"><div class="meta">' + esc(p.meta) + "</div><h3>" + esc(p.title) + "</h3><p>" + esc(p.blurb) + "</p>" +
      '<span class="more">Read case study ' + arrow + "</span></div></button>";
  }).join("");

  var dlg = $("#caseDlg");
  function openCase(id) {
    var p = P.filter(function (x) { return x.id === id; })[0]; if (!p) return;
    var h = '<div class="cv">' + ART[p.art]() + '<button class="x" type="button" aria-label="Close" data-close><svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" stroke-width="1.6"><path d="M2 2l10 10M12 2L2 12"/></svg></button></div>';
    h += '<div class="cb"><div class="meta mono" style="font-size:12px;color:var(--ink-3)">' + esc(p.meta) + '</div><h3 id="caseTitle">' + esc(p.title) + '</h3><p class="sum">' + esc(p.sum) + "</p>";
    if (p.kpis) h += '<div class="kpis">' + p.kpis.map(function (k) { return "<div><b>" + esc(k[0]) + "</b><span>" + esc(k[1]) + "</span></div>"; }).join("") + "</div>";
    h += '<div class="cols">' + p.cols.map(function (c) { return "<div><h4>" + esc(c[0]) + "</h4><ul>" + c[1].map(function (li) { return "<li>" + li + "</li>"; }).join("") + "</ul></div>"; }).join("") + "</div>";
    h += '<div class="tags" style="margin-top:26px">' + p.tags.map(function (t) { return "<span>" + esc(t) + "</span>"; }).join("") + "</div>";
    if (p.links.length) h += '<div class="links">' + p.links.map(function (l) {
      var ext = /^https?:/.test(l[1]);
      return '<a class="btn' + (l[2] ? " primary" : "") + '" href="' + esc(l[1]) + '"' + (ext ? ' target="_blank" rel="noopener"' : "") + (l[1].charAt(0) === "#" ? " data-close" : "") + ">" + esc(l[0]) + (ext ? " ↗" : " " + arrow) + "</a>";
    }).join("") + "</div>";
    h += "</div>";
    dlg.innerHTML = h;
    if (id === "lab" && window.labSummary) { var el = dlg.querySelector("[data-lab-summary]"); if (el) el.innerHTML = window.labSummary(); }
    dlg.showModal(); dlg.scrollTop = 0;
  }
  grid_.addEventListener("click", function (e) { var b = e.target.closest("[data-p]"); if (b) openCase(b.getAttribute("data-p")); });
  document.querySelectorAll("dialog.case").forEach(function (d) {
    d.addEventListener("click", function (e) { if (e.target === d || e.target.closest("[data-close]")) d.close(); });
  });

  window.observeReveal();
})();
