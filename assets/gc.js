/* Shared client helpers: API access, formatting, and the explainable scorecard components used by both the
   employee page and the management dashboard. Contains no data — everything is fetched from /api after sign-in,
   and the server decides what each user may receive. */
(function () {
  "use strict";
  var GC = {};

  GC.api = function (path, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
    if (opts.body !== undefined) {
      init.method = opts.method || "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401 && !opts.noRedirect) {
          window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
          throw new Error("Sign in required");
        }
        if (!r.ok) { var e = new Error(data.error || ("Request failed (" + r.status + ")")); e.status = r.status; throw e; }
        return data;
      });
    });
  };

  GC.esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  };

  GC.money = function (n) {
    var sign = n < 0 ? "-" : ""; n = Math.abs(n);
    if (n >= 1000000) return sign + "RM" + (n / 1000000).toFixed(2) + "M";
    if (n >= 10000) return sign + "RM" + Math.round(n / 1000) + "K";
    return sign + "RM" + Math.round(n).toLocaleString();
  };

  GC.fmt = function (v, unit) {
    if (v == null) return "—";
    if (unit === "RM") return GC.money(v);
    if (unit === "%") return (+v).toFixed(1) + "%";
    if (unit === "days") return (+v).toFixed(1) + " days";
    if (unit === "x") return (+v).toFixed(2) + "x";
    return String(v);
  };

  // Gap text that respects direction (for DSO / turnaround, lower is better).
  GC.gapText = function (k) {
    var behind = k.direction === "lower" ? k.actual > k.target : k.actual < k.target;
    var abs = Math.abs(k.actual - k.target);
    var amount = k.unit === "%" ? abs.toFixed(1) + " pts" : GC.fmt(abs, k.unit);
    if (abs < 1e-9) return '<span class="gap-pos">On target</span>';
    if (k.direction === "lower") return behind ? '<span class="gap-neg">' + amount + " over</span>" : '<span class="gap-pos">' + amount + " under</span>";
    return behind ? '<span class="gap-neg">−' + amount + "</span>" : '<span class="gap-pos">+' + amount + "</span>";
  };

  GC.kind = function (pct) { return pct < 70 ? "critical" : pct < 90 ? "warning" : "good"; };

  GC.meter = function (pct, kind) {
    kind = kind || GC.kind(pct);
    var color = kind === "critical" ? "var(--status-critical)" : kind === "warning" ? "var(--status-warning)" : "var(--status-good)";
    var w = Math.max(0, Math.min(100, pct));
    return '<div class="meter-wrap"><div class="meter-track"><div class="meter-fill" style="width:' + w + "%;background:" + color + ';"></div></div><span class="meter-val">' + Math.round(pct) + "%</span></div>";
  };

  GC.badge = function (kind, text) { return '<span class="badge ' + kind + '"><span class="dot"></span>' + GC.esc(text) + "</span>"; };

  GC.weightBar = function (fin) {
    var non = 100 - fin;
    return '<div class="wbar" role="img" aria-label="Financial ' + fin + "%, non-financial " + non + '%">' +
      (fin > 0 ? '<div class="fin" style="width:' + fin + '%">' + (fin >= 12 ? "Financial " + fin + "%" : fin + "%") + "</div>" : "") +
      (non > 0 ? '<div class="nonfin" style="width:' + non + '%">' + (non >= 12 ? "Non-financial " + non + "%" : non + "%") + "</div>" : "") +
      "</div>";
  };

  GC.kpiWeightRows = function (kpis) {
    return kpis.map(function (k) {
      return '<div class="kpi-wrow"><span>' + GC.esc(k.name) + '</span><div class="track"><div style="width:' + k.points_possible + "%;background:" +
        (k.bucket === "financial" ? "var(--fin)" : "var(--nonfin)") + '"></div></div><span class="pts">' + k.points_possible.toFixed(1) + " pts</span></div>";
    }).join("");
  };

  GC.spark = function (trend, w, h) {
    w = w || 120; h = h || 30;
    var vals = trend.map(function (t) { return typeof t === "number" ? t : t.overall; });
    var lo = Math.min.apply(null, vals.concat([60])), hi = Math.max.apply(null, vals.concat([100]));
    var pts = vals.map(function (v, i) { return [4 + i * (w - 8) / Math.max(1, vals.length - 1), h - 4 - (v - lo) / (hi - lo || 1) * (h - 8)]; });
    var last = pts[pts.length - 1];
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="Score trend ' + vals.join(", ") + '">' +
      '<polyline fill="none" stroke="var(--series-1)" stroke-width="2" points="' + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") + '"/>' +
      '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3" fill="var(--series-1)"/></svg>';
  };

  GC.sourceTag = function (src) {
    if (!src) return "";
    return '<span class="src-tag' + (src.type === "ai_approved" ? " ai" : "") + '" title="' + GC.esc(src.label) + '">' +
      (src.type === "ai_approved" ? "AI-suggested · manager approved" : "Role template") + "</span>";
  };

  GC.scoreTiles = function (card) {
    var f = card.buckets.financial, n = card.buckets.non_financial;
    function tile(id, label, value, sub) {
      return '<button type="button" class="card score-tile" data-explain="' + id + '"><div class="k"><span>' + label + '</span><span class="hint">How is this calculated? →</span></div>' +
        '<div class="v">' + value + '</div><div class="s">' + sub + "</div></button>";
    }
    return '<div class="score-tiles">' +
      tile("overall", "Overall performance", card.overall.toFixed(0) + "%", GC.badge(card.band.kind, card.band.label) + " · " + GC.esc(card.period)) +
      tile("financial", "Financial", f.points.toFixed(0) + "% <small>of " + f.weight + "% possible</small>",
        f.weight ? f.attainment_pct.toFixed(0) + "% of financial targets achieved" : "Not weighted for this role") +
      tile("non_financial", "Non-financial", n.points.toFixed(0) + "% <small>of " + n.weight + "% possible</small>",
        n.weight ? n.attainment_pct.toFixed(0) + "% of non-financial targets achieved" : "Not weighted for this role") +
      "</div>";
  };

  GC.kpiTable = function (card) {
    var big = card.biggest_gap && card.biggest_gap.key;
    var rows = card.kpis.map(function (k) {
      return '<tr class="' + (k.key === big ? "biggest" : "") + '"><td><b>' + GC.esc(k.name) + '</b><div class="proj-meta" title="' + GC.esc(k.formula) + '">' + GC.esc(k.formula) + "</div>" + GC.sourceTag(k.source) + "</td>" +
        '<td><span class="bucket-chip ' + k.bucket + '">' + (k.bucket === "financial" ? "Financial" : "Non-financial") + "</span></td>" +
        '<td class="num">' + GC.fmt(k.target, k.unit) + (k.direction === "lower" ? '<div class="proj-meta">lower is better</div>' : "") + "</td>" +
        '<td class="num">' + GC.fmt(k.actual, k.unit) + "</td>" +
        '<td class="num">' + GC.gapText(k) + "</td>" +
        '<td class="num">' + k.weight_overall.toFixed(1) + '%<div class="proj-meta">' + k.bucket_weight + "% × " + k.weight_in_bucket + "%</div></td>" +
        "<td>" + GC.meter(k.attainment_pct) + "</td>" +
        '<td class="num">' + k.points_earned.toFixed(1) + ' <span class="muted">/ ' + k.points_possible.toFixed(1) + "</span></td></tr>";
    }).join("");
    return '<div style="overflow-x:auto;"><table class="data-table"><thead><tr><th>KPI</th><th>Type</th><th class="num">Target (agreed)</th><th class="num">Actual</th><th class="num">Gap</th><th class="num">Weight</th><th>Progress to target</th><th class="num">Points</th></tr></thead><tbody>' +
      rows + "</tbody><caption>Points = weight × min(actual ÷ target, 100%). Highlighted row = the KPI costing the most points. Hover a formula for the definition.</caption></table></div>";
  };

  GC.weightCard = function (card, opts) {
    opts = opts || {};
    var w = card.weightage;
    var who = w.individual ? "Agreed individually" + (w.agreed_by ? " with " + GC.esc(w.agreed_by) : "") + (w.agreed_at ? " on " + w.agreed_at.slice(0, 10) : "") +
      (w.financial !== w.template_financial ? " (role template is " + w.template_financial + "% financial)" : "") : "Standard template for your role (" + GC.esc(card.employee.title) + ")";
    return '<div class="card"><h3>' + (opts.title || "Your KPI weightage") + '</h3><p class="card-sub">' + who + "</p>" +
      GC.weightBar(w.financial) +
      '<div class="wlegend"><span><i style="background:var(--fin)"></i>Financial KPIs ' + w.financial + '%</span><span><i style="background:var(--nonfin)"></i>Non-financial KPIs ' + w.non_financial + "%</span></div>" +
      '<div style="margin-top:12px;">' + GC.kpiWeightRows(card.kpis) + "</div>" +
      (opts.readOnlyNote ? '<div class="notice lock" style="margin:12px 0 0;">' + opts.readOnlyNote + "</div>" : "") + "</div>";
  };

  // ---------- explanation modal ----------
  var overlay = null;
  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.hidden = true;
    overlay.innerHTML = '<div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="gcModalTitle" style="max-width:760px;">' +
      '<div class="modal-head"><div><h2 id="gcModalTitle"></h2><p class="modal-sub" id="gcModalSub"></p></div>' +
      '<button class="modal-close" type="button" aria-label="Close">&times;</button></div><div class="modal-divider"></div><div class="modal-body" id="gcModalBody"></div></div>';
    (document.getElementById("root") || document.body).appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) GC.closeModal(); });
    overlay.querySelector(".modal-close").addEventListener("click", GC.closeModal);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !overlay.hidden) GC.closeModal(); });
    return overlay;
  }
  GC.openModal = function (title, sub, html) {
    var o = ensureOverlay();
    o.querySelector("#gcModalTitle").textContent = title;
    o.querySelector("#gcModalSub").textContent = sub || "";
    o.querySelector("#gcModalBody").innerHTML = html;
    o.hidden = false;
    return o.querySelector("#gcModalBody");
  };
  GC.closeModal = function () { if (overlay) overlay.hidden = true; };

  GC.explain = function (card, focus) {
    var w = card.weightage;
    function bucketTable(b) {
      var ks = card.kpis.filter(function (k) { return k.bucket === b; });
      var bw = card.buckets[b].weight;
      if (!ks.length) return '<p class="card-sub">No KPIs in this group.</p>';
      var rows = ks.map(function (k) {
        var credit = Math.min(k.attainment_pct, 100);
        return "<tr" + (focus === b ? ' style="background:color-mix(in srgb,var(--series-1) 6%,transparent)"' : "") + "><td>" + GC.esc(k.name) + "</td>" +
          '<td class="num">' + GC.fmt(k.target, k.unit) + '</td><td class="num">' + GC.fmt(k.actual, k.unit) + "</td>" +
          '<td class="num">' + k.attainment_pct.toFixed(1) + "%</td>" +
          '<td class="calc">' + bw + "% × " + k.weight_in_bucket + "% = " + k.points_possible.toFixed(1) + " pts × " + credit.toFixed(1) + "%</td>" +
          '<td class="num"><b>' + k.points_earned.toFixed(1) + "</b></td></tr>";
      }).join("");
      return '<div class="tscroll"><table class="data-table"><thead><tr><th>KPI</th><th class="num">Target</th><th class="num">Actual</th><th class="num">Achieved</th><th>Calculation</th><th class="num">Points</th></tr></thead><tbody>' + rows + "</tbody></table></div>" +
        '<div class="sumline"><span>' + card.buckets[b].label + " subtotal</span><b>" + card.buckets[b].points.toFixed(1) + " of " + bw + " pts</b></div>";
    }
    var lost = card.kpis.slice().sort(function (a, b) { return b.points_lost - a.points_lost; }).filter(function (k) { return k.points_lost > 0.05; });
    var maxLost = lost.length ? lost[0].points_lost : 1;
    var html =
      '<div class="explain-step"><h4>1 · Weightage applied</h4>' + GC.weightBar(w.financial) +
      '<p class="card-sub" style="margin:4px 0 0;">' + (w.individual ? "Individually agreed split" + (w.agreed_by ? " (agreed with " + GC.esc(w.agreed_by) + ")" : "") : "Role template split") +
      ". Each KPI's weight inside its group is then multiplied by the group weight.</p></div>" +
      '<div class="explain-step"><h4>2 · Financial KPIs</h4>' + bucketTable("financial") + "</div>" +
      '<div class="explain-step"><h4>3 · Non-financial KPIs</h4>' + bucketTable("non_financial") + "</div>" +
      '<div class="explain-step"><h4>4 · Total</h4><div class="formula">Financial ' + card.buckets.financial.points.toFixed(1) + " + Non-financial " + card.buckets.non_financial.points.toFixed(1) +
      " = <b>" + card.overall.toFixed(1) + "%</b> overall</div>" +
      '<p class="card-sub">Rule: ' + GC.esc(card.formula.cap) + ". Lower-is-better KPIs (e.g. days) use target ÷ actual.</p></div>" +
      '<div class="explain-step"><h4>5 · Where the missing ' + (100 - card.overall).toFixed(1) + " points went</h4>" +
      (lost.length ? lost.map(function (k) {
        return '<div class="lost-row"><span>' + GC.esc(k.name) + '</span><div class="track"><div style="width:' + (k.points_lost / maxLost * 100).toFixed(0) + '%"></div></div><span class="num" style="text-align:right">−' + k.points_lost.toFixed(1) + "</span></div>";
      }).join("") : '<p class="card-sub">No points lost — every KPI met its target.</p>') + "</div>" +
      '<div class="explain-step"><h4>6 · Trend with the same weights</h4><div class="row">' + GC.spark(card.trend, 220, 44) +
      '<span class="card-sub" style="margin:0;">' + card.trend.map(function (t) { return t.period + ": " + t.overall.toFixed(0) + "%"; }).join(" · ") + "</span></div></div>";
    GC.openModal("How " + (card.employee.name ? card.employee.name.split(" ")[0] + "'s" : "this") + " " + card.overall.toFixed(0) + "% was calculated",
      card.employee.title + " · " + card.period + " · every figure below comes from the agreed targets and recorded actuals", html);
  };

  GC.wireExplain = function (host, card) {
    host.querySelectorAll("[data-explain]").forEach(function (b) {
      b.addEventListener("click", function () { GC.explain(card, b.dataset.explain); });
    });
  };

  GC.toast = function (msg) {
    var t = document.createElement("div");
    t.className = "toast"; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  };

  GC.theme = function (root, btn) {
    var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    try { var saved = localStorage.getItem("gc-theme"); if (saved) dark = saved === "dark"; } catch (e) { /* storage unavailable */ }
    function apply() { root.setAttribute("data-theme", dark ? "dark" : "light"); if (btn) btn.textContent = dark ? "Switch to light" : "Switch to dark"; }
    if (btn) btn.addEventListener("click", function () { dark = !dark; try { localStorage.setItem("gc-theme", dark ? "dark" : "light"); } catch (e) { /* ignore */ } apply(); });
    apply();
  };

  GC.logout = function () {
    GC.api("/api/logout", { body: {}, noRedirect: true }).finally(function () { window.location.href = "/login"; });
  };

  GC.levelLabel = { director: "Director", senior_manager: "Senior Manager", manager: "Manager", employee: "Employee" };

  window.GC = GC;
})();
