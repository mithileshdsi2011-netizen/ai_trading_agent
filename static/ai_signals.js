/* AI Signals dashboard renderer — uses /api/data .signals array */
(function(){
  const colors = {
    green: '#22c55e', greenBg: 'rgba(34,197,94,0.15)',
    red: '#ef4444', redBg: 'rgba(239,68,68,0.15)',
    yellow: '#eab308', yellowBg: 'rgba(234,179,8,0.15)',
    blue: '#60a5fa', blueBg: 'rgba(96,165,250,0.15)',
    orange: '#f97316', orangeBg: 'rgba(249,115,22,0.15)',
    purple: '#a78bfa', purpleBg: 'rgba(167,139,250,0.15)',
    gray: '#9ca3af', grayBg: 'rgba(156,163,175,0.15)'
  };

  function num(v){ return Number(v||0); }
  function pct(v){ const n=num(v); return (n*100).toFixed(0)+'%'; }
  function fmt2(v){ return Number(v||0).toFixed(2); }
  function rupee(v){ return '₹' + Number(v||0).toLocaleString('en-IN',{maximumFractionDigits:2}); }

  function decision(s){
    const bd = (s.bot_decision || '').toLowerCase();
    const act = (s.action || '').toUpperCase();
    if (bd.includes('will buy')) return {label:'BUY', color:'green', icon:'🟢'};
    if (act === 'SELL') return {label:'SELL', color:'red', icon:'🔴'};
    if (bd.includes('already held')) return {label:'WATCH', color:'blue', icon:'🔵'};
    if (bd.includes('max positions')) return {label:'WATCH', color:'purple', icon:'🟣'};
    if (bd.includes('re-entry') || bd.includes('cooldown')) return {label:'WATCH', color:'yellow', icon:'🟡'};
    if (bd.includes('score') || bd.includes('r:r') || bd.includes('confidence')) return {label:'SKIP', color:'orange', icon:'🟠'};
    return {label:'WATCH', color:'gray', icon:'⚪'};
  }

  function rejectionBucket(s){
    const bd = (s.bot_decision || '').toLowerCase();
    const act = (s.action || '').toUpperCase();
    if (bd.includes('will buy')) return null;
    if (act === 'SELL') return 'SELL Signal';
    if (bd.includes('already held')) return 'Already Holding';
    if (bd.includes('max positions')) return 'Capital Limit';
    if (bd.includes('re-entry') || bd.includes('cooldown')) return 'Re-entry Cooldown';
    if (bd.includes('score')) return 'Low Trade Score';
    if (bd.includes('r:r')) return 'Low R:R';
    if (bd.includes('confidence')) return 'Low Confidence';
    if (!s.mtf_aligned && act === 'BUY') return 'MTF Failed';
    return 'Other';
  }

  function missingFor(sym, s){
    const bd = (s.bot_decision || '').toLowerCase();
    const score = num(s.overall_score);
    const conf = num(s.confidence);
    const rr = num(s.risk_reward_ratio);
    if (bd.includes('already held')) return 'Already holding this stock';
    if (bd.includes('max positions')) return 'Position slots are full';
    if (bd.includes('re-entry') || bd.includes('cooldown')) return 'Re-entry cooldown active';
    if (bd.includes('score')) return `Need +${Math.max(1, Math.ceil(60 - score))} score`;
    if (bd.includes('r:r')) return `Need R:R >= 1.5 (currently ${fmt2(rr)})`;
    if (bd.includes('confidence')) return `Need +${Math.max(1, Math.ceil((0.55 - conf)*100))}% confidence`;
    if (!s.mtf_aligned && s.action === 'BUY') return 'MTF not aligned';
    return 'Did not pass quality gate';
  }

  function scoreColorClass(score){
    if (score >= 75) return {txt:'green', bg:colors.greenBg, border:colors.green};
    if (score >= 60) return {txt:'yellow', bg:colors.yellowBg, border:colors.yellow};
    return {txt:'red', bg:colors.redBg, border:colors.red};
  }

  function setText(id, text){ const el=document.getElementById(id); if(el) el.textContent=text; }
  function setHtml(id, html){ const el=document.getElementById(id); if(el) el.innerHTML=html; }

  // ── Main render entry ─────────────────────────────────────────────
  window.renderAiSignals = function(d){
    const all = d.signals || [];
    const buys = all.filter(s => (s.bot_decision||'').toLowerCase().includes('will buy'));
    const rejects = all.filter(s => !(s.bot_decision||'').toLowerCase().includes('will buy'));
    const pipeline = {
      universe: d.stocks_scanned || all.length || 0,
      technicalBuy: all.filter(s => (s.action||'').toUpperCase() === 'BUY').length,
      aiBuy: all.filter(s => (s.action||'').toUpperCase() === 'BUY' && num(s.overall_score) > 0).length,
      passedScore: all.filter(s => num(s.overall_score) >= 60).length,
      passedRisk: buys.length,
      executed: d.total_trades || d.orders?.length || 0
    };

    renderCards(d, all, buys, pipeline);
    renderWhyNoTrade(all);
    renderPipeline(pipeline);
    renderRejections(all);
    renderClosest(all);
    renderTable(all);
    renderStats(all);
    renderMissed(all);
  };

  function renderCards(d, all, buys, pipeline){
    const regime = d.market_regime || 'UNKNOWN';
    const regColor = regime === 'BULL' ? 'green' : regime === 'BEAR' ? 'red' : 'yellow';
    const regIcon = regime === 'BULL' ? '🟢' : regime === 'BEAR' ? '🔴' : '🟠';

    const cardHtml = `
      <div class="ais-card ${regime==='BULL'?'bg-green-gradient':regime==='BEAR'?'bg-red-gradient':'bg-yellow-gradient'}">
        <div class="ais-card-title">Universe Scanned</div>
        <div class="ais-card-value blue">${pipeline.universe}</div>
      </div>
      <div class="ais-card bg-green-gradient">
        <div class="ais-card-title">Potential BUY Signals <span style="font-weight:400;color:#9ca3af">(pre-risk)</span></div>
        <div class="ais-card-value green">${pipeline.aiBuy}</div>
      </div>
      <div class="ais-card bg-green-gradient">
        <div class="ais-card-title">Passed Trade Score</div>
        <div class="ais-card-value green">${pipeline.passedScore}</div>
      </div>
      <div class="ais-card bg-blue-gradient">
        <div class="ais-card-title">Orders Executed Today</div>
        <div class="ais-card-value blue">${pipeline.executed}</div>
      </div>
      <div class="ais-card ${regime==='BULL'?'bg-green-gradient':regime==='BEAR'?'bg-red-gradient':'bg-yellow-gradient'}">
        <div class="ais-card-title">Market Regime</div>
        <div class="ais-card-value ${regColor}">${regIcon} ${regime}</div>
      </div>
    `;
    setHtml('ais-summary-cards', cardHtml);
  }

  function renderWhyNoTrade(all){
    const wrap = document.getElementById('ais-why-no-trade');
    if(!wrap) return;
    if(!all.length){
      setHtml('ais-why-no-trade', '<div style="font-size:13px;color:#6b7280">Scanning market — no signal data yet.</div>');
      wrap.style.display = '';
      return;
    }
    const buys = all.filter(s => (s.bot_decision||'').toLowerCase().includes('will buy'));
    if(buys.length){
      setHtml('ais-why-no-trade', `<div style="display:flex;align-items:center;gap:12px;justify-content:space-between;flex-wrap:wrap">
        <div>
          <div style="font-size:13px;font-weight:700;color:#f9fafb">🟢 BUY conditions were met for <b style="color:#22c55e">${buys.length}</b> stock${buys.length!==1?'s':''}</div>
          <div style="font-size:11px;color:#9ca3af;margin-top:4px">They were not executed because of risk/capital filters (e.g. already held, max positions, re-entry cooldown, low R:R after score review).</div>
        </div>
        <div style="font-size:11px;color:#9ca3af;max-width:260px">Top executable candidate: <b style="color:#f9fafb">${buys[0].symbol}</b> (score ${num(buys[0].overall_score)})</div>
      </div>`);
      wrap.style.display = '';
      return;
    }
    const counts = {};
    all.forEach(s => { const b = rejectionBucket(s); if(b) counts[b] = (counts[b]||0)+1; });
    const top = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,3);
    if(!top.length){
      wrap.style.display = 'none';
      return;
    }
    const topReason = top[0];
    const others = top.slice(1).map(([r,c])=>`${r}: ${c}`).join(' · ') || 'none';
    setHtml('ais-why-no-trade', `<div style="display:flex;align-items:center;gap:12px;justify-content:space-between;flex-wrap:wrap">
      <div>
        <div style="font-size:13px;font-weight:700;color:#f9fafb">🔴 Why No Trade Today?</div>
        <div style="font-size:11px;color:#9ca3af;margin-top:4px">No BUY executed. Top barrier: <b style="color:${colors.orange}">${topReason[0]}</b> (${topReason[1]} stock${topReason[1]!==1?'s':''}).</div>
      </div>
      <div style="font-size:11px;color:#9ca3af;max-width:260px">Other: ${others}</div>
    </div>`);
    wrap.style.display = '';
  }

  function renderPipeline(p){
    const steps = [
      {label:'Universe Scanned', count:p.universe, color:'#60a5fa'},
      {label:'Technical BUY', count:p.technicalBuy, color:'#22c55e'},
      {label:'Potential BUY', count:p.aiBuy, color:'#22c55e'},
      {label:'Passed Trade Score', count:p.passedScore, color:'#eab308'},
      {label:'Passed Risk Filters', count:p.passedRisk, color:'#f97316'},
      {label:'Orders Executed', count:p.executed, color:'#a78bfa'}
    ];
    const max = Math.max(1, p.universe);
    const html = steps.map((st,i)=>`
      <div class="ais-pipeline-step" style="flex:1">
        <div class="ais-funnel-bar" style="height:${Math.max(8, (st.count/max)*80)}px;background:${st.color}"></div>
        <div class="ais-funnel-count" style="color:${st.color}">${st.count}</div>
        <div class="ais-funnel-label">${st.label}</div>
        ${i < steps.length-1 ? '<div class="ais-funnel-arrow">↓</div>' : ''}
      </div>
    `).join('');
    setHtml('ais-pipeline', html);
  }

  function renderRejections(all){
    const cats = ['Low Trade Score','MTF Failed','Low R:R','Already Holding','Re-entry Cooldown','Capital Limit','Low Confidence','SELL Signal','Other'];
    const counts = {};
    cats.forEach(c=>counts[c]=0);
    all.forEach(s=>{ const b=rejectionBucket(s); if(b) counts[b]=(counts[b]||0)+1; });
    const total = Math.max(1, Object.values(counts).reduce((a,b)=>a+b,0));
    const catColors = {
      'Low Trade Score':colors.orange,'MTF Failed':colors.purple,'Low R:R':colors.yellow,'Already Holding':colors.blue,
      'Re-entry Cooldown':colors.gray,'Capital Limit':colors.purple,'Low Confidence':colors.red,'SELL Signal':colors.red,'Other':colors.gray
    };
    const html = cats.filter(c=>counts[c]>0).map(c=>{
      const pctVal = (counts[c]/total*100).toFixed(1);
      return `
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
            <span style="color:${catColors[c]}">${c}</span>
            <span style="color:#e2e8f0;font-weight:700">${counts[c]}</span>
          </div>
          <div class="progress-bar" style="height:8px;background:#1f2937;border-radius:4px;overflow:hidden">
            <div class="progress-fill" style="width:${pctVal}%;background:${catColors[c]};border-radius:4px"></div>
          </div>
        </div>`;
    }).join('');
    setHtml('ais-rejection-bars', html || '<div style="color:#6b7280;font-size:13px">No rejections recorded yet.</div>');
  }

  function renderClosest(all){
    const closest = all
      .filter(s => (s.action||'').toUpperCase() === 'BUY' && !(s.bot_decision||'').toLowerCase().includes('will buy'))
      .sort((a,b) => num(b.overall_score) - num(a.overall_score))
      .slice(0,5);
    if (!closest.length){
      setHtml('ais-closest', '<div style="color:#6b7280;font-size:13px;padding:12px 0">No stocks close to qualifying.</div>');
      return;
    }
    const html = closest.map(s=>{
      const score = num(s.overall_score);
      const conf = Math.round(num(s.confidence)*100);
      const miss = missingFor(s.symbol, s);
      const sc = scoreColorClass(score);
      return `
        <div class="ais-closest-card" style="border:1px solid ${sc.border};background:${sc.bg}">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <span style="font-weight:700;color:#f9fafb;font-size:14px">${s.symbol}</span>
            <span style="font-weight:700;color:${colors[sc.txt]}">${score}</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;font-size:11px;color:#d1d5db;margin-bottom:6px">
            <div>Score <b style="color:${colors[sc.txt]}">${score}</b></div>
            <div>Conf <b>${conf}%</b></div>
            <div>R:R <b>${fmt2(s.risk_reward_ratio)}</b></div>
          </div>
          <div style="font-size:11px;color:#9ca3af;margin-bottom:4px">MTF: ${s.mtf_aligned?'Aligned ✓':'Not aligned'} | Sector: ${s.sector||'—'}</div>
          <div style="font-size:11px;color:${colors.orange};font-weight:600">Missing: ${miss}</div>
        </div>`;
    }).join('');
    setHtml('ais-closest', html);
  }

  let tableSignals = [];
  function renderTable(all){
    tableSignals = all;
    const thead = `
      <tr>
        <th>Symbol <span class="ais-sort-btn" data-sort="symbol" onclick="sortAiTable('symbol')">⇅</span></th>
        <th>Sector</th>
        <th>Trade Score <span class="ais-sort-btn" data-sort="score" onclick="sortAiTable('score')">⇅</span></th>
        <th>AI Score <span class="ais-sort-btn" data-sort="ai" onclick="sortAiTable('ai')">⇅</span></th>
        <th>Confidence <span class="ais-sort-btn" data-sort="conf" onclick="sortAiTable('conf')">⇅</span></th>
        <th>R:R <span class="ais-sort-btn" data-sort="rr" onclick="sortAiTable('rr')">⇅</span></th>
        <th>MTF</th>
        <th>RSI</th>
        <th>Decision</th>
        <th>Missing Req.</th>
        <th>Reason</th>
      </tr>`;
    setHtml('ais-candidate-thead', thead);
    populateFilters(all);
    const body = buildTableBody(all);
    setHtml('ais-candidate-body', body || '<tr><td colspan="11" style="text-align:center;color:#6b7280;padding:20px">No signals to display</td></tr>');
    updateSortIcons();
  }

  function buildTableBody(rows){
    return rows.map(s=>{
      const d = decision(s);
      const score = num(s.overall_score);
      const conf = Math.round(num(s.confidence)*100);
      const sc = scoreColorClass(score);
      const rsi = s.rsi ? Math.round(num(s.rsi)) : '—';
      return `
        <tr class="ais-candidate-row" data-symbol="${s.symbol}" onclick="showSignalDetail('${s.symbol}')">
          <td><b style="color:#f9fafb">${s.symbol}</b></td>
          <td style="color:#9ca3af;font-size:12px">${s.sector||'—'}</td>
          <td style="text-align:center"><span class="ais-score-pill" style="background:${sc.bg};color:${colors[sc.txt]};border-color:${sc.border}">${score}</span></td>
          <td style="text-align:center"><span style="color:#93c5fd">${score}</span></td>
          <td style="text-align:center">${conf}%</td>
          <td style="text-align:center;color:${s.risk_reward_ratio>=1.5?'#22c55e':s.risk_reward_ratio>=1?'#eab308':'#ef4444'}">${fmt2(s.risk_reward_ratio)}</td>
          <td style="text-align:center;color:${s.mtf_aligned?'#22c55e':'#ef4444'};font-size:12px">${s.mtf_aligned?'✓':'✗'}</td>
          <td style="text-align:center">${rsi}</td>
          <td style="text-align:center"><span class="ais-decision-pill ${d.color}" style="background:${colors[d.color+'Bg']};color:${colors[d.color]};border:1px solid ${colors[d.color]}">${d.icon} ${d.label}</span></td>
          <td style="font-size:11px;color:${colors.orange};max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${missingFor(s.symbol,s)}</td>
          <td style="font-size:11px;color:#9ca3af;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(s.reasoning||s.bot_decision||'—').substring(0,35)}</td>
        </tr>`;
    }).join('');
  }

  window.sortAiTable = function(key){
    const dir = (window._lastSortKey === key && window._lastSortDir !== 'desc') ? 'desc' : 'asc';
    window._lastSortKey = key; window._lastSortDir = dir;
    const sorted = [...tableSignals].sort((a,b)=>{
      let va, vb;
      if (key === 'symbol') { va = a.symbol; vb = b.symbol; }
      else if (key === 'score') { va = num(a.overall_score); vb = num(b.overall_score); }
      else if (key === 'ai') { va = num(a.overall_score); vb = num(b.overall_score); }
      else if (key === 'conf') { va = num(a.confidence); vb = num(b.confidence); }
      else if (key === 'rr') { va = num(a.risk_reward_ratio); vb = num(b.risk_reward_ratio); }
      if (typeof va === 'string') return dir==='asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      return dir==='asc' ? va - vb : vb - va;
    });
    setHtml('ais-candidate-body', buildTableBody(sorted));
    updateSortIcons(key, dir);
  };

  window.filterAiTable = function(){
    const q = document.getElementById('ais-search').value.toLowerCase();
    const sector = document.getElementById('ais-filter-sector').value;
    const dec = document.getElementById('ais-filter-decision').value;
    const filtered = tableSignals.filter(s=>{
      const d = decision(s).label;
      return (s.symbol||'').toLowerCase().includes(q)
        && (sector === '' || (s.sector||'') === sector)
        && (dec === '' || d === dec);
    });
    setHtml('ais-candidate-body', buildTableBody(filtered) || '<tr><td colspan="11" style="text-align:center;color:#6b7280;padding:20px">No matching rows</td></tr>');
  };

  function populateFilters(all){
    const sectors = Array.from(new Set(all.map(s=>s.sector||'—').filter(x=>x))).sort();
    const sel = document.getElementById('ais-filter-sector');
    if(sel){
      const current = sel.value;
      sel.innerHTML = '<option value="">All Sectors</option>' + sectors.map(s=>`<option value="${s}">${s}</option>`).join('');
      sel.value = current;
      sel.onchange = function(){ filterAiTable(); };
    }
    const dec = document.getElementById('ais-filter-decision');
    if(dec) dec.onchange = function(){ filterAiTable(); };
    const srch = document.getElementById('ais-search');
    if(srch){
      srch.oninput = function(){ filterAiTable(); };
      srch.onkeyup = function(){ filterAiTable(); };
    }
  }

  function updateSortIcons(key, dir){
    document.querySelectorAll('.ais-sort-btn').forEach(b=>{
      const k = b.dataset.sort;
      b.textContent = k === key ? (dir==='asc' ? '↑' : '↓') : '⇅';
      b.style.color = k === key ? '#60a5fa' : '#6b7280';
    });
  }

  function renderStats(all){
    const buyCnt = all.filter(s => (s.action||'').toUpperCase() === 'BUY').length;
    const sellCnt = all.filter(s => (s.action||'').toUpperCase() === 'SELL').length;
    const skipCnt = all.length - buyCnt - sellCnt;
    const scores = all.map(s=>num(s.overall_score)).filter(x=>x>0);
    const confs = all.map(s=>num(s.confidence)).filter(x=>x>0);
    const rrs = all.map(s=>num(s.risk_reward_ratio)).filter(x=>x>0);
    const avg = arr => arr.length ? (arr.reduce((a,b)=>a+b,0)/arr.length).toFixed(1) : '—';
    const max = arr => arr.length ? Math.max(...arr).toFixed(1) : '—';
    const html = `
      <div class="ais-stat-chip"><span>BUY Signals</span><b class="green">${buyCnt}</b></div>
      <div class="ais-stat-chip"><span>SELL Signals</span><b class="red">${sellCnt}</b></div>
      <div class="ais-stat-chip"><span>SKIP/HOLD</span><b class="orange">${skipCnt}</b></div>
      <div class="ais-stat-chip"><span>Avg Trade Score</span><b>${avg(scores)}</b></div>
      <div class="ais-stat-chip"><span>Highest Score</span><b>${max(scores)}</b></div>
      <div class="ais-stat-chip"><span>Avg Confidence</span><b>${avg(confs)}</b></div>
      <div class="ais-stat-chip"><span>Highest Conf</span><b>${max(confs)}</b></div>
      <div class="ais-stat-chip"><span>Avg R:R</span><b>${avg(rrs)}</b></div>
    `;
    setHtml('ais-today-stats', html);
  }

  function renderMissed(all){
    // Optional: only show after 15:30 IST or if explicitly present
    const hour = new Date().getHours();
    const mins = new Date().getMinutes();
    if (hour < 15 || (hour === 15 && mins < 30)){
      document.getElementById('ais-missed').style.display = 'none';
      return;
    }
    const missed = all.filter(s => decision(s).label === 'SKIP' && (s.action||'').toUpperCase() === 'BUY');
    if (!missed.length){
      document.getElementById('ais-missed').style.display = 'none';
      return;
    }
    document.getElementById('ais-missed').style.display = '';
    const html = missed.slice(0,10).map(s=>`
      <tr>
        <td>${s.symbol}</td>
        <td>${missingFor(s.symbol,s)}</td>
        <td>—</td>
        <td>—</td>
      </tr>
    `).join('');
    setHtml('ais-missed-body', html);
  }

  window.showSignalDetail = function(symbol){
    const s = tableSignals.find(x=>x.symbol===symbol);
    if(!s) return;
    const d = decision(s);
    const score = num(s.overall_score);
    const conf = Math.round(num(s.confidence)*100);
    const miss = missingFor(symbol, s);
    const sc = scoreColorClass(score);
    const bd = s.bot_decision || 'Evaluated';
    const modal = document.getElementById('ais-detail-modal');
    setHtml('ais-detail-content', `
      <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:16px">
        <div>
          <div style="font-size:22px;font-weight:800;color:#f9fafb">${symbol}</div>
          <div style="color:#9ca3af;font-size:12px">${s.sector||'—'} | ${s.trend||'—'} | Regime: ${s.market_regime||'—'}</div>
        </div>
        <span class="ais-decision-pill ${d.color}" style="background:${colors[d.color+'Bg']};color:${colors[d.color]};border:1px solid ${colors[d.color]};padding:4px 10px;border-radius:99px;font-size:13px;font-weight:700">${d.icon} ${d.label}</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px">
        <div class="ais-detail-metric"><div class="ais-detail-label">Trade Score</div><div style="color:${colors[sc.txt]};font-weight:800;font-size:20px">${score}</div></div>
        <div class="ais-detail-metric"><div class="ais-detail-label">AI Overall</div><div style="color:${colors[sc.txt]};font-weight:800;font-size:20px">${score}</div></div>
        <div class="ais-detail-metric"><div class="ais-detail-label">Confidence</div><div style="font-weight:800;font-size:20px">${conf}%</div></div>
        <div class="ais-detail-metric"><div class="ais-detail-label">R:R</div><div style="font-weight:800;font-size:20px">${fmt2(s.risk_reward_ratio)}x</div></div>
        <div class="ais-detail-metric"><div class="ais-detail-label">MTF</div><div style="font-weight:800;font-size:20px;color:${s.mtf_aligned?'#22c55e':'#ef4444'}">${s.mtf_aligned?'✓ Aligned':'✗ Not aligned'}</div></div>
        <div class="ais-detail-metric"><div class="ais-detail-label">Entry / Target / SL</div><div style="font-size:14px;font-weight:700">${rupee(s.price)} / <span class="green">${rupee(s.target)}</span> / <span class="red">${rupee(s.stop_loss)}</span></div></div>
      </div>
      <div class="ais-detail-section">
        <div class="ais-detail-label">Rejection / Final Reason</div>
        <div style="font-size:13px;color:#e2e8f0;line-height:1.6">${bd}</div>
        <div style="font-size:12px;color:${colors.orange};margin-top:6px"><b>Missing requirement:</b> ${miss}</div>
      </div>
      <div class="ais-detail-section">
        <div class="ais-detail-label">Reasoning</div>
        <div style="font-size:12px;color:#9ca3af;line-height:1.6">${(s.reasoning||'No detailed reasoning recorded.').substring(0,500)}</div>
      </div>
      <div style="font-size:11px;color:#6b7280;margin-top:14px">Score component breakdown is not exposed in the current data feed. Showing available signal data only.</div>
    `);
    modal.style.display = 'flex';
  };

  window.closeSignalDetail = function(){
    const m = document.getElementById('ais-detail-modal');
    if(m) m.style.display='none';
  };
})();
