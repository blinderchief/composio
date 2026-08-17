/* Reads the dataset injected into #audit-data (render.py) and builds the page from it.
   The page renders its own JSON, so the chart, the queue, and the table cannot drift. */
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("audit-data").textContent);
  const $ = (s, r) => (r || document).querySelector(s);
  const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c; if (h != null) n.innerHTML = h; return n; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
  const pct = (x) => x == null ? "—" : Math.round(x * 100) + "%";

  const recs = D.records || [];
  const H = D.headline || {};
  const meta = D.meta || {};

  // ---- masthead + preview banner ----
  $("#m-run").textContent = meta.run_id || "—";
  $("#m-date").textContent = (meta.generated_at || "").slice(0, 10) || "—";
  if (meta.status && meta.status !== "complete") {
    $("#preview-banner").innerHTML =
      '<div class="wrap"><div class="banner">Preview build — dataset is <b>' + esc(meta.status) +
      '</b>. The page renders whatever the pipeline has produced so far; numbers fill in as the run completes.</div></div>';
  }

  // ---- 1 · headline ----
  const cat = meta.catalog || {};
  const already = H.already_toolkits, netnew = (H.total || 0) - (already || 0);
  $("#headline").textContent = "The blockers aren't engineering. They're paperwork, and paperwork has lead times.";
  $("#lede").innerHTML =
    "Of " + (H.total || recs.length) + " apps, <b>" + (already || 0) + " are already Composio toolkits</b> (" +
    esc(H.already_toolkits_note || "") + "). Of the rest, <b>" + (H.ship_this_week || 0) +
    " can ship this week</b> with zero human approvals; <b>" + (H.start_outreach_now || 0) +
    " need an approval or partnership</b> you'd have to start today; and " + (H.not_a_toolkit || 0) +
    " aren't toolkits at all. Here is the queue, ordered by when you'd have to begin.";

  // ---- stat strip ----
  const acc = D.accuracy || {};
  const wr = acc.pass2 && acc.pass2.whole_row;
  const stats = [
    { n: already != null ? already : "—", l: "already Composio toolkits", ci: cat.complete ? "authoritative diff" : "lower bound · partial catalog" },
    { n: H.ship_this_week != null ? H.ship_this_week : "—", l: "ship this week · zero approvals" },
    { n: H.start_outreach_now != null ? H.start_outreach_now : "—", l: "start outreach now · long lead", flag: true },
    { n: H.needs_human != null ? H.needs_human : "—", l: "routed to human review", flag: true },
    { n: H.first_party_mcp != null ? H.first_party_mcp : "—", l: "have a first-party MCP" },
    wr && wr.p != null
      ? { n: pct(wr.p), l: "whole-row accuracy vs gold", ci: "95% CI [" + pct(wr.low) + ", " + pct(wr.high) + "]" }
      : { n: "pending", l: "accuracy vs gold set", ci: "awaiting human verification" },
  ];
  const strip = $("#stats");
  stats.forEach(s => {
    const d = el("div", "stat" + (s.flag ? " flag" : ""));
    d.appendChild(el("div", "n", esc(s.n)));
    d.appendChild(el("div", "l", esc(s.l)));
    if (s.ci) d.appendChild(el("div", "ci", esc(s.ci)));
    strip.appendChild(d);
  });

  // ---- 2 · queue lanes ----
  const LANES = [
    { key: "start_outreach_now", name: "Start outreach now", urgent: true },
    { key: "ship_this_week", name: "Ship this week" },
    { key: "unblock_then_ship", name: "Unblock, then ship" },
    { key: "park", name: "Park — needs a human" },
    { key: "not_a_toolkit", name: "Not a toolkit" },
  ];
  const Q = D.queue || [];
  const maxLead = Q.reduce((m, q) => Math.max(m, q.lead_time_days ? q.lead_time_days[1] : 0), 1);
  const lanesEl = $("#lanes");
  LANES.forEach(L => {
    const items = Q.filter(q => q.lane === L.key);
    if (!items.length) return;
    const lane = el("div", "lane" + (L.urgent ? " urgent" : ""));
    const head = el("div", "lane-h");
    head.appendChild(el("span", "dot"));
    head.appendChild(el("span", "name", esc(L.name)));
    head.appendChild(el("span", "cnt", items.length + " app" + (items.length > 1 ? "s" : "")));
    lane.appendChild(head);
    items.forEach(q => {
      const row = el("div", "qi");
      row.appendChild(el("div", "rank", "#" + q.rank));
      row.appendChild(el("div", "app", esc(q.name) + '<span class="cat">' + esc(q.value_signal) + "</span>"));
      row.appendChild(el("div", "act", esc(q.next_action)));
      const meta2 = el("div", "meta");
      const owner = el("span", "tag " + q.owner_hint, esc(q.owner_hint));
      meta2.appendChild(owner);
      if (q.lead_time_days) {
        meta2.appendChild(el("span", "lead", q.lead_time_days[0] + "–" + q.lead_time_days[1] + "d"));
        const bar = el("div", "leadbar"); const i = el("i");
        i.style.width = Math.max(6, (q.lead_time_days[1] / maxLead) * 100) + "%";
        bar.appendChild(i); meta2.appendChild(bar);
      } else {
        meta2.appendChild(el("span", "tag", "effort " + esc(q.effort)));
      }
      row.appendChild(meta2);
      lane.appendChild(row);
    });
    lanesEl.appendChild(lane);
  });
  if (!Q.length) lanesEl.innerHTML = '<p class="small">Queue is generated after the verify stage. Run <code class="mono">make run</code>.</p>';

  // ---- 3 · matrix ----
  const mtx = (D.aggregates && D.aggregates.matrix) || {};
  const cats = Object.keys(mtx).sort();
  const tbl = $("#matrixtbl");
  if (cats.length) {
    const cols = ["self_serve", "gated", "no_api"];
    const labels = { self_serve: "self-serve", gated: "gated", no_api: "not an app" };
    let head = "<tr><th class='cat'>category</th>";
    cols.forEach(c => head += "<th>" + labels[c] + "</th>"); head += "<th>total</th></tr>";
    let body = "";
    cats.forEach(c => {
      const row = mtx[c]; const tot = cols.reduce((s, k) => s + (row[k] || 0), 0);
      body += "<tr><td class='cat'>" + esc(c) + "</td>";
      cols.forEach(k => {
        const v = row[k] || 0;
        body += "<td class='cell'>" + (v || "·") + (k === "gated" && v ? "<span class='hb' style='width:" + (v / Math.max(tot, 1) * 100) + "%'></span>" : "") + "</td>";
      });
      body += "<td class='cell'>" + tot + "</td></tr>";
    });
    tbl.innerHTML = head + body;
  } else { tbl.parentElement.innerHTML = '<p class="small">Matrix appears once records exist.</p>'; }

  // ---- 4 · table ----
  const COLS = [
    { k: "name", t: "app" }, { k: "category", t: "category" }, { k: "auth", t: "auth", m: 1 },
    { k: "gate", t: "gate", m: 1 }, { k: "api_breadth", t: "api", m: 1 }, { k: "mcp", t: "mcp", m: 1 },
    { k: "composio_toolkit", t: "toolkit", m: 1 }, { k: "buildability", t: "verdict", m: 1 },
    { k: "confidence", t: "conf", m: 1 }, { k: "status", t: "status", m: 1 }, { k: "evidence", t: "evidence" },
  ];
  const rowVal = (r, k) => k === "auth" ? r.auth_schemes.join("+") : k === "evidence" ? (r.evidence || []).length : r[k];
  const thead = $("#thead");
  COLS.forEach(c => { const th = el("th", null, esc(c.t)); th.dataset.k = c.k; th.setAttribute("scope", "col"); thead.appendChild(th); });
  const fcat = $("#fcat"), fbuild = $("#fbuild");
  [...new Set(recs.map(r => r.category))].sort().forEach(c => fcat.appendChild(el("option", null, esc(c))));
  [...new Set(recs.map(r => r.buildability))].sort().forEach(c => fbuild.appendChild(el("option", null, esc(c))));

  let sortK = "name", sortDir = 1;
  function draw() {
    const q = $("#q").value.toLowerCase(), fc = fcat.value, fb = fbuild.value, fh = $("#fhuman").checked;
    let rows = recs.filter(r =>
      (!fc || r.category === fc) && (!fb || r.buildability === fb) && (!fh || r.needs_human) &&
      (!q || (r.name + " " + r.auth_schemes.join(" ") + " " + r.gate + " " + r.category + " " + r.buildability).toLowerCase().includes(q)));
    rows.sort((a, b) => { const x = rowVal(a, sortK), y = rowVal(b, sortK); return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
    const tb = $("#tbody"); tb.innerHTML = "";
    rows.forEach(r => {
      const tr = el("tr", r.needs_human ? "needs-human" : "");
      COLS.forEach(c => {
        const td = el("td", c.m ? "m" : "");
        if (c.k === "name") td.innerHTML = "<strong>" + esc(r.name) + "</strong>";
        else if (c.k === "auth") td.textContent = r.auth_schemes.join("+");
        else if (c.k === "confidence") td.innerHTML = '<span class="pill ' + esc(r.confidence) + '">' + esc(r.confidence) + "</span>";
        else if (c.k === "status") td.innerHTML = '<span class="pill ' + esc(r.status) + '">' + esc(r.status) + "</span>";
        else if (c.k === "composio_toolkit") td.textContent = r.composio_toolkit === "exists" ? "✓ exists" : r.composio_toolkit;
        else if (c.k === "evidence") {
          const evs = r.evidence || [];
          td.innerHTML = evs.length
            ? evs.slice(0, 3).map((e, i) => '<a class="evlink" href="' + esc(e.url) + '" target="_blank" rel="noopener" title="' + esc(e.quote_span) + '">[' + (i + 1) + "]</a>").join(" ")
            : '<span class="small">—</span>';
        } else td.textContent = rowVal(r, c.k);
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    $("#rowcount").textContent = rows.length + " / " + recs.length + " rows";
  }
  thead.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k; sortDir = sortK === k ? -sortDir : 1; sortK = k;
    thead.querySelectorAll("th").forEach(t => t.removeAttribute("aria-sort"));
    th.setAttribute("aria-sort", sortDir === 1 ? "ascending" : "descending"); draw();
  }));
  ["q", "fcat", "fbuild", "fhuman"].forEach(id => $("#" + id).addEventListener("input", draw));
  draw();

  // ---- 5 · pipeline + human ----
  const stages = ["seed", "catalog diff", "discover", "fetch", "extract·P1", "verify·P2", "queue", "score", "render"];
  $("#pipe").innerHTML = stages.map((s, i) => (i ? '<span class="ar">→</span>' : "") + '<span class="st">' + esc(s) + "</span>").join("");
  $("#human-count").textContent = H.needs_human != null ? H.needs_human : "—";
  const br = D.browser || {};
  if (br.note) $("#browser-note").innerHTML = "<strong>Browser channel (loop 5):</strong> " + esc(br.note);

  // ---- 6 · verification ----
  const ab = $("#acc-body");
  if (!acc || acc.status === "gold_set_not_verified" || acc.status === "not_run") {
    ab.innerHTML = '<div class="callout warn">' + esc((acc && acc.note) ||
      "Accuracy is scored against an independently verified gold set that has not been generated yet for this run. Run the scorer to populate this section.") + "</div>";
  } else {
    const fields = ["whole_row"]; const p1 = acc.pass1, p2 = acc.pass2;
    let rows = "<tr><th>metric</th><th>pass 1</th><th>pass 2</th><th>Δ</th><th>95% CI (pass 2)</th></tr>";
    const line = (label, a, b) => {
      const up = b.p != null && a.p != null && b.p > a.p;
      return "<tr><td>" + label + "</td><td>" + pct(a.p) + "</td><td class='" + (up ? "up" : "") + "'>" + pct(b.p) +
        "</td><td class='" + (up ? "up" : "") + "'>" + (b.p != null && a.p != null ? (b.p - a.p >= 0 ? "+" : "") + Math.round((b.p - a.p) * 100) + "pt" : "—") +
        "</td><td class='mono'>[" + pct(b.low) + ", " + pct(b.high) + "]</td></tr>";
    };
    rows += line("whole-row", p1.whole_row, p2.whole_row);
    Object.keys(p2.per_field || {}).forEach(f => rows += line(f, p1.per_field[f], p2.per_field[f]));
    let strata = "";
    Object.keys(p2.per_stratum || {}).forEach(s => {
      const v = p2.per_stratum[s]; strata += "<tr><td>" + esc(s) + "</td><td>" + v.k + "/" + v.n + "</td><td>" + pct(v.p) + "</td></tr>";
    });
    const ab2 = p2.abstention || {};
    const fixes = (D.fixes || []).slice(0, 12).map(f => "<li>[" + esc(f.fixed_by_loop) + "] " + esc(f.slug) + " · " + esc(f.field) + ": " + esc(f.from) + " → " + esc(f.to) + "</li>").join("");
    ab.innerHTML =
      '<div class="grid2"><div><h3 style="font-size:16px">Pass 1 → Pass 2 delta (vs gold, n=' + (acc.gold_n || "—") + ')</h3><table class="delta">' + rows + "</table>" +
      '<h3 style="font-size:16px;margin-top:18px">By stratum (pass 2)</h3><table class="delta"><tr><th>stratum</th><th>correct</th><th>rate</th></tr>' + strata + "</table></div>" +
      '<div><h3 style="font-size:16px">What the loops caught</h3><ul class="fixes">' + (fixes || "<li>no fixes logged</li>") + "</ul>" +
      '<h3 style="font-size:16px;margin-top:18px">Abstention quality</h3><p class="small">' +
      (ab2.quality != null ? "<strong>" + pct(ab2.quality) + "</strong> of the pipeline's abstentions were <em>good</em> — it declined exactly where the gold label is also unknown (" + ab2.good_unknown + " good, " + ab2.bad_unknown + " bad). Abstaining correctly beats guessing correctly." : "appears once scored rows include abstentions.") +
      "</p></div></div>" +
      '<div class="callout" style="margin-top:18px">' + esc(acc.interval_caveat || "") + "</div>" +
      (acc.method_note ? '<div class="callout" style="margin-top:12px">' + esc(acc.method_note) + "</div>" : "");
  }

  // ---- 7 · what defeated us ----
  const traps = recs.filter(r => r.ambiguity_note || (r.needs_human && (r.gate === "unknown" || r.auth_schemes.includes("UNKNOWN"))));
  const dl = $("#defeated-list");
  if (traps.length) {
    traps.forEach(r => {
      dl.appendChild(el("div", "d", "<h4>" + esc(r.name) + "</h4><div class='small'>" +
        esc(r.ambiguity_note || r.primary_blocker || "insufficient public evidence") + "</div>"));
    });
  } else { dl.innerHTML = '<p class="small">Ambiguous-entity findings appear here after extraction.</p>'; }

  // ---- JSON-LD ----
  document.getElementById("jsonld-dataset").textContent = JSON.stringify({
    "@context": "https://schema.org", "@type": "Dataset",
    name: "Connector Readiness Audit — 100 apps",
    description: "Agent-researched buildability audit of 100 SaaS apps for Composio toolkits, with a prioritized build-and-outreach queue and gold-scored accuracy.",
    dateModified: meta.generated_at, distribution: [{ "@type": "DataDownload", encodingFormat: "application/json", contentUrl: "./data.json" }],
    variableMeasured: ["auth_schemes", "access_gate", "buildability", "composio_toolkit", "mcp", "confidence"]
  });
})();
