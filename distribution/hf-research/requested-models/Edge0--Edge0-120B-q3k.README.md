---
license: other
license_name: edge0-community-model-1.0
license_link: LICENSE
library_name: edge0
tags:
- moe
- edge-inference
- latent-moe
- 4-bit
- agent
- coding
- cuda
base_model:
- Qwen/Qwen3.8-Flash-Next
pipeline_tag: text-generation
---

<style>
.edge0-card :where(p,span,small,strong,b,summary,div,nav,li,td,th) {font-family:inherit;line-height:inherit;color:inherit;}
.edge0-card{color-scheme:light; --ink:#1d1d1f; --muted:#6e6e73; --line:#e5e5e7;
  --signal:#E63946; --ivory:#F1FAEE; --ice:#A8DADC; --steel:#45789D; --navy:#1D3557; --canvas:#ffffff; --wash:#f5f5f7;}
.edge0-card,.edge0-card *{box-sizing:border-box;}
.edge0-card{margin:0; background:var(--canvas); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Inter,Arial,sans-serif;
  font-size:15px; line-height:1.8; overflow-wrap:break-word; -webkit-font-smoothing:antialiased;}
.edge0-card a{color:#515154; text-decoration:none; text-underline-offset:4px;}
.edge0-card a:hover{color:var(--ink); text-decoration:underline;}
.edge0-card a:focus-visible,.edge0-card summary:focus-visible,.edge0-card [tabindex]:focus-visible{outline:3px solid var(--steel); outline-offset:5px; border-radius:4px;}
.edge0-card > div[align="center"]{text-align:center;}
.edge0-card > div[align="center"] > p{margin:14px 0;}
.edge0-card h1{color:var(--ink); font-size:35px; font-weight:650; letter-spacing:-1px; line-height:1.28;
  margin:30px 0 19px;}
.edge0-card h2{color:var(--ink); font-size:25px; font-weight:600; letter-spacing:-.5px; line-height:1.35;
  margin:58px 0 22px; padding-bottom:13px; border-bottom:1px solid var(--line); scroll-margin-top:24px;}
.edge0-card h3{color:var(--ink); font-size:17px; font-weight:600; margin:28px 0 12px;}
.edge0-card p{margin:17px 0;}
.edge0-card b,.edge0-card strong{font-weight:600; color:var(--ink);}
.edge0-card img{max-width:100%; height:auto; border-radius:10px; vertical-align:middle;}
.edge0-card img.badge{width:auto; height:20px; max-width:100%; border-radius:0; margin:3px;}
.edge0-card .hero-image{display:block; width:100%; margin:0 auto 28px;}
.edge0-card .card-hero > p:has(> .hero-image){margin:0;}
.edge0-card .card-hero .hero-image{margin:0; border-radius:0;}
.edge0-card .card-hero > h1{color:var(--ink); font-size:24px; font-weight:500; letter-spacing:-.6px;
  line-height:1.3; margin:46px 0 0;}
.edge0-card .editorial-art{margin:0; line-height:0;}
.edge0-card .editorial-image{display:block; width:100%; max-width:720px; height:auto; margin:0 auto;}
.edge0-card .editorial-pair{position:relative; display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  align-items:start; gap:64px; max-width:720px; margin:0 auto; padding:64px 0 70px;}
.edge0-card .editorial-pair::after{content:""; position:absolute; left:50%; top:50%;
  width:1px; height:72px; background:#d2d2d7; transform:translate(-50%,-50%);}
.edge0-card .editorial-pillar{min-width:0; text-align:center;}
.edge0-card .editorial-pillar .pillar-title{max-width:240px; margin:0 auto 13px; color:var(--ink);
  font-size:25px; font-weight:500; letter-spacing:-.55px; line-height:1.25;}
.edge0-card .editorial-pillar .pillar-caption{max-width:205px; margin:0 auto; color:var(--muted);
  font-size:12px; line-height:1.7;}
.edge0-card .card-hero > p.badge-github{margin:0 0 8px; line-height:1;}
.edge0-card .card-hero > p.badge-models{display:flex; flex-wrap:nowrap; width:max-content;
  max-width:100%; margin:0 auto; padding:0 0 7px; overflow-x:auto; white-space:nowrap;
  line-height:1; scrollbar-width:thin; scrollbar-color:#d2d2d7 #fafafa;
  -webkit-overflow-scrolling:touch;}
.edge0-card .badge-models > a{flex:0 0 auto;}
.edge0-card .model-context{max-width:740px; margin:50px auto 48px;}
.edge0-card .model-context p{color:var(--muted); font-size:14px; line-height:1.9;}
.edge0-card .model-context strong{color:var(--ink); font-weight:500;}
.edge0-card .quality-figure-link{display:block;}
.edge0-card .quality-chart{display:block; width:100%; max-width:1400px; margin:30px auto 34px; border-radius:0;}
.edge0-card .repo-summary{margin:58px 0 38px;}
.edge0-card .repo-summary > p:first-child{margin:0 0 17px; font-size:14px;}
.edge0-card .repo-summary > p:first-child strong{white-space:nowrap;}
.edge0-card .repo-summary > p:last-child{margin:16px 0 0;}
.edge0-card .repo-note{display:block; position:relative; padding-left:19px; text-indent:-19px;
  font-size:12px; line-height:1.8; color:var(--muted);}
.edge0-card .repo-note strong{color:inherit; font-weight:500;}
.edge0-card > div[align="center"] > p:first-child{margin:0;}
.edge0-card > div[align="center"] .section-nav{margin:28px 0 36px; line-height:2.5;}
.edge0-card .section-nav a{display:inline-block; color:#6e6e73; font-size:12px; margin:0 6px;}
.edge0-card .section-nav a:hover{color:var(--ink);}
.edge0-card code{background:#f5f5f7; color:#515154; padding:2px 6px; border-radius:4px; font-size:87%;
  font-family:"SF Mono",Menlo,Consolas,monospace; overflow-wrap:anywhere;}
.edge0-card pre{background:#f5f5f7; color:var(--ink); padding:20px 22px; border:1px solid #e5e5e7;
  border-radius:8px; overflow-x:auto; max-width:100%; line-height:1.7; margin:22px 0;}
.edge0-card pre code{background:none; color:inherit; padding:0; font-size:13px; overflow-wrap:normal;}
.edge0-card .table-scroll{max-width:100%; overflow-x:auto; margin:25px 0; border:1px solid var(--line);
  border-radius:8px; -webkit-overflow-scrolling:touch; scrollbar-width:thin;
  scrollbar-color:#d2d2d7 #f5f5f7;}
.edge0-card table{border-collapse:collapse; width:100%; font-size:13px; margin:0;}
.edge0-card th,.edge0-card td{border-bottom:1px solid #e5e5e7; padding:12px 13px; text-align:left; min-width:68px;}
.edge0-card th{background:#f5f5f7; color:#6e6e73; font-weight:500; white-space:nowrap; font-size:11px;}
.edge0-card tr:last-child td{border-bottom:0;}
.edge0-card tr:nth-child(even) td{background:#fafafa;}
.edge0-card tr.current-model td,.edge0-card tr.current-build td{background:#f5f5f7;}
.edge0-card tr.current-model td:first-child,.edge0-card tr.current-build td:first-child{box-shadow:inset 3px 0 #d2d2d7;}
.edge0-card tr.current-model td:first-child{color:var(--ink);}
.edge0-card td:first-child{min-width:155px;}
.edge0-card .speed-table-wrap,.edge0-card .speed-legend{--speed-header:var(--signal);
  --speed-highlight:#D8EEEF;}
.edge0-card .speed-table-wrap{border:0; scrollbar-color:var(--steel) var(--ivory);}
.edge0-card .speed-table{color:var(--navy);}
.edge0-card .speed-table th,.edge0-card .speed-table td{border:0;}
.edge0-card .speed-table th{color:var(--ivory); background:var(--speed-header);
  font-weight:700; padding-top:7px; padding-bottom:7px;}
.edge0-card .speed-table tbody tr td{background:#fff;}
.edge0-card .speed-table tbody tr.current-build td{background:var(--speed-highlight);}
.edge0-card .speed-table tr.current-build td:first-child{box-shadow:none;}
.edge0-card .speed-table tr.current-build td:nth-child(2){color:var(--navy); font-weight:600;}
.edge0-card .speed-table strong{color:var(--navy);}
.edge0-card .speed-legend{display:inline-block; color:var(--steel); font-size:12px; line-height:1.7;}
.edge0-card .speed-legend::before{content:""; display:inline-block; width:8px; height:8px;
  margin-right:8px; border-radius:2px; background:var(--ice);}
.edge0-card .speed-legend strong{color:var(--navy); font-weight:500;}
.edge0-card blockquote{margin:22px 0; padding:4px 20px; border-left:2px solid #d2d2d7;
  background:#fafafa; color:#6e6e73;}
.edge0-card blockquote p{margin:10px 0;}
.edge0-card hr{border:none; border-top:1px solid var(--line); margin:38px 0;}
.edge0-card ul,.edge0-card ol{padding-left:23px; margin:16px 0;}
.edge0-card li{margin:8px 0; padding-left:2px;}
.edge0-card li::marker{color:#86868b;}
.edge0-card details{margin:24px 0; padding:0 0 0; border-top:1px solid var(--line);
  border-bottom:1px solid var(--line);}
.edge0-card details + details{border-top:0; margin-top:-24px;}
.edge0-card summary{color:#515154; font-weight:500; font-size:14px; line-height:1.6;
  padding:17px 4px; cursor:pointer; list-style:none; display:flex; align-items:center; gap:12px;}
.edge0-card summary::-webkit-details-marker{display:none;}
.edge0-card summary::before{content:""; width:6px; height:6px; border-right:1.5px solid #86868b;
  border-bottom:1.5px solid #86868b; transform:rotate(-45deg); flex-shrink:0; margin:0 3px;
  transition:transform .16s ease;}
.edge0-card details[open] > summary::before{transform:rotate(45deg) translate(-1px,-1px);}
.edge0-card summary:hover{color:var(--ink);}
.edge0-card details[open]{padding-bottom:20px;}
.edge0-card details[open] > summary{margin-bottom:2px;}
.edge0-card details > :not(summary){margin-left:3px; margin-right:3px;}
.edge0-card details > :last-child{margin-bottom:0;}
.edge0-card details .table-scroll{margin-top:13px;}
.edge0-card details p{font-size:14px; color:var(--muted);}
.edge0-card .demo-section{margin:58px 0 0; border:0; scroll-margin-top:24px;}
.edge0-card .demo-section > summary{padding:0 0 13px; border-bottom:1px solid var(--line);
  display:grid; grid-template-columns:8px minmax(0,1fr); column-gap:14px; row-gap:7px;
  font-size:25px; font-weight:600; letter-spacing:-.5px; line-height:1.35; color:var(--ink);}
.edge0-card .demo-section > summary > .demo-title{grid-column:2; grid-row:1; margin:0; padding:0;
  border:0; font:inherit; color:inherit; letter-spacing:inherit;}
.edge0-card .demo-section > summary > .demo-model-note{grid-column:2; grid-row:2;
  font-size:12px; font-weight:400; line-height:1.65; letter-spacing:0; color:var(--steel);}
.edge0-card .demo-section[open]{padding-bottom:0;}
.edge0-card .demo-section[open] > summary{margin-bottom:0;}
.edge0-card .demo-section > summary::before{grid-column:1; grid-row:1; border-color:var(--steel);}
.edge0-card .showcase-player{max-width:480px; margin:32px auto 48px;}
.edge0-card .showcase-screen{position:relative;}
.edge0-card .showcase-video{display:block; width:100%; height:auto; aspect-ratio:16/9; object-fit:contain;
  border-radius:12px; background:#000; box-shadow:0 8px 32px #1D35570C;}
.edge0-card .showcase-play{position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
  display:grid; place-items:center; width:54px; height:54px; border:1px solid #FFFFFFAA;
  border-radius:50%; background:linear-gradient(135deg,#8EE4CF,#55BFC4 34%,#ADA0D4 66%,#F1B5CC);
  box-shadow:inset 0 2px 7px #FFFFFF55,0 4px 18px #1D355733; cursor:pointer;}
.edge0-card .showcase-play::after{content:""; display:block; margin-left:4px; width:0; height:0;
  border-top:9px solid transparent; border-bottom:9px solid transparent; border-left:15px solid white;}
.edge0-card .showcase-play[hidden]{display:none;}
.edge0-card .showcase-play:focus-visible{outline:3px solid var(--steel); outline-offset:4px;}
.edge0-card .showcase-meta{display:flex; gap:16px; align-items:center; justify-content:space-between;
  margin:14px 0 22px; color:var(--steel); font-size:11px; line-height:1.5;}
.edge0-card .showcase-meta small{font-size:inherit;}
.edge0-card .showcase-direct{color:var(--steel); font-size:11px; text-decoration:none;}
.edge0-card .showcase-seek-head{display:flex; justify-content:space-between; align-items:baseline; gap:16px;
  min-height:20px; color:var(--navy); font-size:12px;}
.edge0-card .showcase-current{font-weight:500;}
.edge0-card .showcase-time{flex:none; color:var(--steel); font-size:11px; font-variant-numeric:tabular-nums;}
.edge0-card .showcase-seek{position:relative; height:40px; margin:0 8px;}
.edge0-card .showcase-segments{position:absolute; inset:17px 0 auto; height:6px; display:flex; gap:3px; pointer-events:none;}
.edge0-card .showcase-segments span{height:6px; border-radius:4px; background:linear-gradient(to right,
  var(--steel) var(--chapter-fill,0%),var(--ice) var(--chapter-fill,0%));}
.edge0-card .showcase-slider{-webkit-appearance:none; appearance:none; display:block; position:absolute; inset:0;
  width:calc(100% + 16px); height:40px; margin:0 -8px; padding:0; background:transparent; cursor:pointer;}
.edge0-card .showcase-slider::-webkit-slider-runnable-track{height:6px; background:transparent;}
.edge0-card .showcase-slider::-moz-range-track{height:6px; background:transparent;}
.edge0-card .showcase-slider::-webkit-slider-thumb{-webkit-appearance:none; width:16px; height:16px; margin-top:-5px;
  border:2px solid white; border-radius:50%; background:var(--signal); box-shadow:0 0 0 1px #E6394655,0 2px 6px #1D355722;}
.edge0-card .showcase-slider::-moz-range-thumb{width:12px; height:12px; border:2px solid white; border-radius:50%;
  background:var(--signal); box-shadow:0 0 0 1px #E6394655;}
.edge0-card .showcase-slider:focus-visible{outline:2px solid var(--steel); outline-offset:2px; border-radius:6px;}
.edge0-card .demo-chapters{display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin-top:3px;}
.edge0-card .demo-chapters a{min-width:0; display:flex; flex-direction:column; align-items:flex-start; gap:5px;
  padding:10px 8px; border-radius:7px; color:var(--steel); font-size:11px; line-height:1.45; text-decoration:none;
  transition:background .15s ease,color .15s ease;}
.edge0-card .demo-chapters small{font-size:10px; letter-spacing:.3px; font-variant-numeric:tabular-nums;}
.edge0-card .demo-chapters a[aria-current="true"]{background:#A8DADC33; color:var(--navy);}
.edge0-card .demo-chapters a:hover{background:#A8DADC26; color:var(--navy);}
.edge0-card .demo-chapters a:focus-visible{outline:2px solid var(--steel); outline-offset:2px;}
.edge0-card{width:100%;max-width:100%;min-width:0;text-align:left;}
.edge0-card p{font-size:15px;}
.edge0-card table{display:table;width:100%;border-collapse:collapse!important;border-spacing:0;border:0;border-radius:0;overflow:visible;}
.edge0-card .speed-table{font-size:13px;}
.edge0-card .speed-table,.edge0-card .speed-table thead,.edge0-card .speed-table tbody,.edge0-card .speed-table tr,.edge0-card .speed-table th,.edge0-card .speed-table td{border:0!important;}
.edge0-card .badge-github a,.edge0-card .badge-models a{display:inline-block!important;}
.edge0-card .card-hero .badge{margin:3px;height:20px;width:auto;vertical-align:middle;}
.edge0-card .demo-section>summary>.demo-title{margin:0;padding:0;border:0;font:inherit;letter-spacing:inherit;color:inherit;grid-column:2;grid-row:1;}
.edge0-card .showcase-screen{max-width:480px;margin:0 auto;}
.edge0-card .showcase-screen video{width:100%;max-width:480px;height:auto;margin:0;}
.edge0-card .showcase-screen .video-container{width:100%;max-width:480px;margin:0;}
.edge0-card .demo-chapters{grid-template-columns:repeat(auto-fit,minmax(76px,1fr));}
</style>

<div class="edge0-card model-card">
<div align="center" class="card-hero">
<p><img alt="Edge0" class="hero-image" src="assets/hero.png" width="100%"/></p>
<h1 id="edge0-120b-q3_k">Edge0-120B · Q3_K</h1>
<div align="center" class="editorial-art">
<p><img alt="Think big. Run here. A 120B agent. On one consumer GPU. Sparse. By design. Latent MoE. Trained natively at 4 bits." class="editorial-image" src="assets/editorial-pair.svg" width="720"/></p>
</div>
<p align="center" class="badge-github"><a href="https://github.com/Edge0-AI/Edge0"><img alt="GitHub" class="badge" height="20" src="assets/badges/github-badge.svg"/></a> <a href="https://github.com/Edge0-AI/Edge0/releases"><img alt="Windows releases" class="badge" height="20" src="assets/badges/windows-releases-badge.svg"/></a></p>
<p align="center" class="badge-models"><a href="https://huggingface.co/Edge0/Edge0-145B-q3k"><img alt="Edge0-145B-q3k" class="badge" height="20" src="assets/badges/Edge0-145B-q3k-badge.svg"/></a> <a href="https://huggingface.co/Edge0/Edge0-145B-NVFP4"><img alt="Edge0-145B-NVFP4" class="badge" height="20" src="assets/badges/Edge0-145B-NVFP4-badge.svg"/></a> <a href="https://huggingface.co/Edge0/Edge0-120B-NVFP4"><img alt="Edge0-120B-NVFP4" class="badge" height="20" src="assets/badges/Edge0-120B-NVFP4-badge.svg"/></a></p>
<p class="section-nav"><a href="#quality">Quality</a> · <a href="#speed">Speed</a> · <a href="#architecture">Architecture</a> · <a href="#quick-start">Quick start</a></p>
</div>
<hr/>
<div class="model-context">
<p><strong>A 120B agent for local inference.</strong></p>
<p><strong>144.6 tok/s agent decode · 4576.5 tok/s 16K prefill · 75.3 terminal-bench 2.1.</strong></p>
<p>Throughput measured on <strong>RTX 5090 D 24 GB · Q3_K</strong>.</p>
</div>
<p>A latent sparse MoE: 48 layers, 512 experts, 10 active per token, 256K context, trained natively at 4 bits.</p>
<div class="repo-summary">
<p><strong>This repository:</strong> Edge0-120B · <strong>Q3_K </strong> </p>
<p><small class="repo-note">ⓘ <strong>120B Q3_K</strong> was tested with a <strong>16 GiB VRAM + 20 GiB host RAM</strong> budget on RTX 5090 D 24 GB. These are test limits, not minimum system requirements; see the speed table and residency figures below.</small></p>
</div>
<details open="">
<summary><b>Model overview</b></summary>
<p><strong>Q3_K · RTX 5090 D 24 GB.</strong> Prefill throughput uses a 16K-token input; weight sizes are listed separately for each format.</p>
<p><a href="https://huggingface.co/Edge0/Edge0-120B-q3k/resolve/main/assets/model-overview.svg" title="Open full-size model overview"><img alt="Edge0-120B Q3_K model overview: architecture, benchmark scores, throughput and weight sizes" src="assets/model-overview.svg" width="100%"/></a></p>
</details>
<h2 id="quality">Quality</h2>
<p>Higher is better. Results are listed by model.</p>
<p><a class="quality-figure-link" href="https://huggingface.co/Edge0/Edge0-120B-q3k/resolve/main/assets/quality/quality-overview.svg" title="Open full-size chart"><img alt="Six-model Quality comparison: DeepSWE 1.1, Terminal-Bench 2.1, HLE, GPQA-Diamond, IFBench, AA-LCR. All panels use a 0–100 scale. Full model names, scores and missing values are shown in the chart." class="quality-chart" src="assets/quality/quality-overview.svg" width="1400"/></a></p>
<h2 id="speed">Speed</h2>
<p>Throughput is shown in <strong>tok/s</strong>, grouped by hardware and build. The summary above uses <strong>Q3_K</strong> on RTX 5090 D 24 GB.</p>
<details open>
<summary><b>Full speed table</b> · all reported machines and builds</summary>
<p><small class="speed-legend">Highlighted rows show the <strong>Q3_K</strong> build in this repository.</small></p>
<div aria-label="Model card table; scroll horizontally to see all columns" class="table-scroll speed-table-wrap" role="region" tabindex="0"><table class="speed-table">
<thead>
<tr>
<th>Machine</th>
<th>Build</th>
<th style="text-align: right;">4K prefill</th>
<th style="text-align: right;">16K prefill</th>
<th style="text-align: right;">Chat decode</th>
<th style="text-align: right;">Agent decode</th>
</tr>
</thead>
<tbody>
<tr class="current-build">
<td>RTX 5090 D 24 GB</td>
<td>Q3_K</td>
<td style="text-align: right;">3236.7</td>
<td style="text-align: right;">4576.5</td>
<td style="text-align: right;">130.2</td>
<td style="text-align: right;"><strong>144.6</strong></td>
</tr>
<tr>
<td>RTX 5090 D 24 GB</td>
<td>NVFP4</td>
<td style="text-align: right;">2668.5</td>
<td style="text-align: right;">4357.1</td>
<td style="text-align: right;">124.9</td>
<td style="text-align: right;">135.5</td>
</tr>
<tr class="current-build">
<td>RTX 4090 24 GB</td>
<td>Q3_K</td>
<td style="text-align: right;">2637.5</td>
<td style="text-align: right;">3551.1</td>
<td style="text-align: right;">95.0</td>
<td style="text-align: right;">104.9</td>
</tr>
<tr class="current-build">
<td>RTX Spark N1X 60 GiB</td>
<td>Q3_K</td>
<td style="text-align: right;">987.0</td>
<td style="text-align: right;">1081.7</td>
<td style="text-align: right;">47.2</td>
<td style="text-align: right;">68.4</td>
</tr>
<tr>
<td>RTX Spark N1X 60 GiB</td>
<td>NVFP4</td>
<td style="text-align: right;">996.4</td>
<td style="text-align: right;">1066.9</td>
<td style="text-align: right;">45.1</td>
<td style="text-align: right;">68.9</td>
</tr>
<tr class="current-build">
<td>Apple M2 Ultra (Metal)</td>
<td>Q3_K</td>
<td style="text-align: right;">867.6</td>
<td style="text-align: right;">908.7</td>
<td style="text-align: right;">60.4</td>
<td style="text-align: right;">63.0</td>
</tr>
<tr class="current-build">
<td>16 GiB VRAM + 20 GiB RAM</td>
<td>Q3_K</td>
<td style="text-align: right;">430</td>
<td style="text-align: right;">778</td>
<td style="text-align: right;">62.4</td>
<td style="text-align: right;">73.3</td>
</tr>
<tr>
<td>16 GiB VRAM + 20 GiB RAM</td>
<td>NVFP4</td>
<td style="text-align: right;">361.1</td>
<td style="text-align: right;">619.7</td>
<td style="text-align: right;">51.3</td>
<td style="text-align: right;">59.9</td>
</tr>
</tbody>
</table></div>
</details>
<details open>
<summary><b>Measurement conditions</b> · constrained budget and memory residency</summary>
<p><code>16 GiB VRAM + 20 GiB RAM</code> rows are the RTX 5090 D 24 GB again, constrained by cgroup to a 16 GiB VRAM and
20 GiB system-RAM budget — not a different card. With residency on, the same card holds <strong>Q3_K</strong> (27.98 GiB of weights)
as 22.93 GiB VRAM + 18.40 GiB RAM, and <strong>NVFP4</strong> (32.96 GiB) as 23.02 GiB VRAM + 23.40 GiB RAM — disjoint expert sets,
nothing counted twice.</p>
</details>
<details class="demo-section" id="in-action" open>
<summary><h2 class="demo-title">In action</h2><small class="demo-model-note">These demonstrations were recorded with Edge0-145B.</small></summary>
<div class="showcase-player" data-duration="121.301043">
<div class="showcase-screen">
<video aria-label="Edge0-145B demonstrations" class="showcase-video" controls="" playsinline="" poster="https://huggingface.co/Edge0/Edge0-120B-q3k/resolve/main/assets/demos/showcase-frame.jpg" preload="metadata" width="480">
<source src="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4" type="video/mp4"/>
<track kind="chapters" label="Chapters" src="https://huggingface.co/Edge0/Edge0-120B-q3k/resolve/main/assets/demos/showcase-chapters.vtt" srclang="en"/>
<a href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/blob/main/assets/demos/showcase.mp4">Watch the Edge0 demonstrations</a>
</video>
</div>
<p class="showcase-meta"><small>Recorded with Edge0-145B</small><a class="showcase-direct" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/blob/main/assets/demos/showcase.mp4">Open video ↗</a></p>
<nav aria-label="Video chapters" class="demo-chapters">
<a aria-label="Local Chat &amp; Coding, 0:00" data-end="36.56666666666667" data-start="0.0" data-title="Local Chat &amp; Coding" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4#t=0.0"><small>0:00</small><span>Chat &amp; Coding</span></a>
<a aria-label="Video Production, 0:36" data-end="58.4" data-start="36.56666666666667" data-title="Video Production" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4#t=36.56666666666667"><small>0:36</small><span>Video Production</span></a>
<a aria-label="Manhattan in 3D, 0:58" data-end="79.56666666666666" data-start="58.4" data-title="Manhattan in 3D" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4#t=58.4"><small>0:58</small><span>Manhattan in 3D</span></a>
<a aria-label="Cathedral World Building, 1:19" data-end="98.93333333333334" data-start="79.56666666666666" data-title="Cathedral World Building" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4#t=79.56666666666666"><small>1:19</small><span>3D Cathedral</span></a>
<a aria-label="PCB Design, 1:38" data-end="121.301043" data-start="98.93333333333334" data-title="PCB Design" href="https://huggingface.co/Edge0/Edge0-145B-NVFP4/resolve/main/assets/demos/showcase.mp4#t=98.93333333333334"><small>1:38</small><span>PCB Design</span></a>
</nav>
</div>
</details>
<h2 id="architecture">Architecture</h2>
<details open>
<summary><b>Architecture details</b> · diagram, configuration and expert routing</summary>
<p><a href="https://huggingface.co/Edge0/Edge0-120B-q3k/resolve/main/assets/arch.svg" title="Open full-size architecture diagram"><img alt="Edge0-120B architecture and expert routing" src="assets/arch.svg" width="100%"/></a></p>
<div aria-label="Model card table; scroll horizontally to see all columns" class="table-scroll" role="region" tabindex="0"><table>
<thead>
<tr>
<th></th>
<th>Edge0-120B</th>
</tr>
</thead>
<tbody>
<tr>
<td>Total parameters</td>
<td><strong>120B</strong> · <strong>8.64 B active per token</strong></td>
</tr>
<tr>
<td>Layers</td>
<td>48 · hybrid attention, full every 4 layers + sparse indexer</td>
</tr>
<tr>
<td>Experts</td>
<td><strong>512 per layer</strong>, 10 routed + 1 shared active per token, SwiGLU width 640</td>
</tr>
<tr>
<td>Expert pool</td>
<td>60.4 B parameters at latent width ℓ = <strong>1280</strong></td>
</tr>
<tr>
<td>PLE</td>
<td><strong>51.2 B</strong> n-gram embedding table, fp8, memory-mapped</td>
</tr>
<tr>
<td>MTP head</td>
<td>1.26 B draft model, 512 experts, for speculative decode</td>
</tr>
<tr>
<td>Context</td>
<td>256K · KV int8 with 32K resident</td>
</tr>
<tr>
<td>Builds</td>
<td>Q3_K 27.98 GiB · NVFP4 32.96 GiB</td>
</tr>
</tbody>
</table></div>
<p><strong>Left</strong>: the stack the model actually runs — token embedding, 48 hybrid-attention layers, final norm, lm_head.
<strong>Right</strong>: one layer opened up. Attention reads and writes the full 2560 width. <strong>D</strong> then projects the token down to
ℓ per routing group, the router picks 10 of 512 experts, each expert runs ℓ → 640 → ℓ, a shared expert runs
alongside, and <strong>U</strong> projects the combined result back to 2560.</p>
<p>Everything outside the expert pool stays at 2560: D and U are shared, one copy per layer. Only the 512 experts work at
ℓ. That is why an expert is a fixed <strong>1,164,800 B</strong> record — the engine keeps a few thousand of them on the card and streams
the rest from host memory, at a cost that tracks how many experts are resident and not how big the model is.</p>
</details>
<h2 id="quick-start">Quick start</h2>
<p>Each model folder (Q3_K or NVFP4, Edge0-145B or Edge0-120B) runs on <a href="https://github.com/Edge0-AI/Edge0">Edge0</a>
through <code>edge0-infer</code>, from the command line or from the Edge0 Chat desktop app. Keep the folder as it is:
<code>edge0-infer</code> identifies the model by <code>model_name</code> in its <code>model.json</code>.</p>
<details open>
<summary><b>Platform &amp; documentation notes</b></summary>
<p>The build instructions target NVIDIA GPUs with CUDA 13 on Linux or Windows (x64 or ARM64). The Apple M2 Ultra measurements use Metal; they do not establish macOS release availability.</p>
</details>
<details open="">
<summary><b>Build &amp; command-line instructions</b></summary>
<p><strong>1. Build.</strong> Clone the repository, then build two parts:</p>
<pre><code class="language-bash">git clone https://github.com/Edge0-AI/Edge0.git edge0
</code></pre>
<ul>
<li><strong>edge0-strata</strong>, the engine: follow "Build (Linux, NVIDIA)" or "Build (Windows, NVIDIA)" in <a href="https://github.com/Edge0-AI/Edge0/blob/main/edge0-strata/EDGE0.md">edge0-strata/EDGE0.md</a>.</li>
<li><strong>edge0-infer</strong>: follow "Build" (Linux) or "Windows" in <a href="https://github.com/Edge0-AI/Edge0/blob/main/edge0-infer/docs/getting-started.md">edge0-infer/docs/getting-started.md</a>.</li>
</ul>
<p><strong>2. Run from the command line.</strong> Pass one model folder, such as <code>Edge0-120B-q3k</code>, not the folder that holds several of
them. On Windows the program is <code>build\windows\bin\edge0-infer.exe</code>.</p>
<pre><code class="language-bash">build/cpu/bin/edge0-infer serve --model /path/to/Edge0-120B-q3k
</code></pre>
<p>It prints <code>Listening: http://127.0.0.1:8000 model=&lt;name&gt;</code> once the weights are loaded, where <code>&lt;name&gt;</code> is <code>edge0_120b</code>,
and then serves OpenAI- and Anthropic-compatible endpoints:</p>
<pre><code class="language-bash">curl localhost:8000/v1/chat/completions -H 'content-type: application/json' -d '{"model":"edge0_120b","messages":[{"role":"user","content":"hi"}],"max_tokens":64}'
</code></pre>
<p><code>edge0-infer chat --model /path/to/Edge0-120B-q3k</code> opens an interactive session instead. More options:
<a href="https://github.com/Edge0-AI/Edge0/blob/main/edge0-infer/docs/edge0-120b.md">edge0-infer/docs/edge0-120b.md</a>.</p>
</details>
<details open>
<summary><b>Desktop app</b> · Windows</summary>
<p>⬇️ <strong>Download:</strong> <strong><a href="https://github.com/Edge0-AI/Edge0/releases">GitHub Releases</a></strong></p>
<p>Open <strong>Edge0 Chat</strong> on Windows and choose a model folder, such as <code>Edge0-120B-q3k</code>, to run the model locally. To build the app from source, follow the <a href="https://github.com/Edge0-AI/Edge0/blob/main/edge0-infer/examples/desktop/README.md">Edge0 Chat</a> guide.</p>
</details>
<h2 id="acknowledgements">Acknowledgements</h2>
<details open>
<summary><b>Credits &amp; license</b></summary>
<p>We thank the teams behind <a href="https://huggingface.co/Qwen/Qwen3.8-Flash-Next">Qwen3.8-Flash-Next</a> and <a href="https://github.com/Niko1221/Strata">Strata</a> for sharing their work with the community. Edge0 extends the Strata engine with latent MoE support.</p>
<p><strong>Model license.</strong> Edge0-specific contributions are provided under the <a href="LICENSE">Edge0 Community Model License 1.0</a>, only to the extent that Edge0 owns or is authorized to license them. Qwen materials remain subject to the <a href="third-party/Qwen-LICENSE.txt">Qwen Community License 1.0</a>. Both sets of applicable terms must be followed; see <a href="NOTICE">NOTICE</a> and the <a href="release-scope.json">release scope</a>.</p>
<p>The Edge0 license permits personal use, research, modification, noncommercial sharing, internal use, and commercial use of generated outputs, subject to applicable upstream conditions. Selling or commercially bundling the model, operating a commercial model service, or offering an independent commercial coding or office assistant using it requires a separate written Edge0 license. See LICENSE for the precise scope and exceptions.</p>
<p><strong>Commercial licensing:</strong> <a href="mailto:info@edge0.ai">info@edge0.ai</a>. Edge0 permission does not waive Qwen's separate authorization or attribution requirements. Framework code retains its Apache-2.0 license; Strata retains its MIT license. Existing valid grants are not revoked.</p>
</details>
</div>
