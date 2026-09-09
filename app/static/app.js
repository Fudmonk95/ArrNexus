(function(){
  const esc=(v)=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

  function lifecycle(x){
    const rows=(x.milestones||[]);
    if(!rows.length)return '<span class="muted">—</span>';
    return `<div class="milestones">${rows.map(step=>`<span class="milestone stage-${esc(step.stage)}" title="${esc(step.detail||'')}">${esc(step.label||step.stage)}</span>`).join('')}</div>`;
  }

  function rowHtml(x){
    return `<tr data-stage="${esc(x.stage)}">
      <td><strong>${esc(x.title)}</strong>${x.year?`<div class="muted">${esc(x.year)}</div>`:''}</td>
      <td>${esc(x.requested_by||'—')}</td><td>${esc(x.source||'—')}</td><td>${esc(x.service||'—')}</td>
      <td>${lifecycle(x)}</td><td><span class="stage stage-${esc(x.stage)}">${esc(x.stage_label||x.stage)}</span></td>
      <td>${esc(x.detail||'')}</td><td class="mono small path-cell">${esc(x.zurg_path||'—')}</td>
    </tr>`;
  }

  async function pollPipeline(){
    if(!window.ArrNexusPipeline)return;
    try{
      const r=await fetch('/api/pipeline/live',{headers:{'Accept':'application/json'},cache:'no-store'});
      const data=await r.json();
      if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);
      for(const [k,v] of Object.entries(data.summary||{})){
        const el=document.querySelector(`[data-stat="${k}"]`);if(el)el.textContent=v;
      }
      const body=document.querySelector('#pipeline-table tbody');
      if(body)body.innerHTML=(data.rows||[]).map(rowHtml).join('')||'<tr><td colspan="8" class="empty">Tracker is warming up or there is no current activity.</td></tr>';
      const updated=document.querySelector('#pipeline-updated');if(updated)updated.textContent=data.updated_at||'warming up';
      const live=document.querySelector('#pipeline-live-state');
      if(live){live.textContent=data.stale?'STALE':'LIVE';live.classList.toggle('stale',!!data.stale)}
    }catch(e){
      const live=document.querySelector('#pipeline-live-state');if(live){live.textContent='DELAYED';live.classList.add('stale')}
      console.debug('pipeline poll:',e.message);
    }
    setTimeout(pollPipeline,2000);
  }

  async function copyTarget(button){
    const selector=button.getAttribute('data-copy');
    const target=selector?document.querySelector(selector):null;
    if(!target)return;
    const text=target.textContent||'';
    try{
      await navigator.clipboard.writeText(text);
      const before=button.textContent;button.textContent='Copied';setTimeout(()=>button.textContent=before,1300);
    }catch(_){
      const range=document.createRange();range.selectNodeContents(target);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
    }
  }

  // ------------------------------------------------------------------
  // Magic Intake 2.0 (v13.1)
  // ------------------------------------------------------------------
  const magic={type:'all',groupKey:'',mediaType:'movie',results:[]};

  function magicCards(){return Array.from(document.querySelectorAll('#magic-grid .magic-card'))}

  function applyMagicFilters(){
    if(!window.ArrNexusMagicIntake)return;
    const search=(document.querySelector('#magic-filter-search')?.value||'').trim().toLowerCase();
    const genre=(document.querySelector('#magic-filter-genre')?.value||'all').toLowerCase();
    const theme=(document.querySelector('#magic-filter-theme')?.value||'all').toLowerCase();
    const state=(document.querySelector('#magic-filter-state')?.value||'all').toLowerCase();
    let visible=0;
    for(const card of magicCards()){
      const type=(card.dataset.mediaType||'').toLowerCase();
      const genres=(card.dataset.genres||'').split('|').filter(Boolean);
      const themes=(card.dataset.themes||'').split('|').filter(Boolean);
      const cardState=(card.dataset.state||'').toLowerCase();
      const stateBucket=['queued','importing','verifying'].includes(cardState)?'importing':cardState;
      const okType=magic.type==='all'||type===magic.type;
      const okSearch=!search||(card.dataset.search||'').includes(search);
      const okGenre=genre==='all'||genres.includes(genre);
      const okTheme=theme==='all'||themes.includes(theme);
      const okState=state==='all'||stateBucket===state;
      const show=okType&&okSearch&&okGenre&&okTheme&&okState&&!card.classList.contains('magic-stale');
      card.hidden=!show;if(show)visible++;
    }
    const count=document.querySelector('#magic-visible-count');if(count)count.textContent=visible;
    const empty=document.querySelector('#magic-filter-empty');if(empty)empty.hidden=visible!==0;
  }

  function setMagicType(type,button){
    magic.type=type;
    document.querySelectorAll('[data-magic-type]').forEach(x=>x.classList.toggle('active',x===button));
    applyMagicFilters();
  }

  function modal(open){
    const el=document.querySelector('#magic-match-modal');if(!el)return;
    el.hidden=!open;el.setAttribute('aria-hidden',open?'false':'true');
    document.body.classList.toggle('modal-open',open);
    if(open)setTimeout(()=>document.querySelector('#magic-match-query')?.focus(),10);
  }

  async function searchMagicMatch(){
    const q=(document.querySelector('#magic-match-query')?.value||'').trim();
    const results=document.querySelector('#magic-match-results');
    if(!q||!results)return;
    results.innerHTML='<p class="empty">Searching…</p>';
    try{
      const url=`/api/magic-intake/lookup?media_type=${encodeURIComponent(magic.mediaType)}&q=${encodeURIComponent(q)}`;
      const r=await fetch(url,{cache:'no-store',headers:{'Accept':'application/json'}});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      magic.results=data.results||[];
      results.innerHTML='';
      if(!magic.results.length){results.innerHTML='<p class="empty">No Arr lookup results found.</p>';return}
      for(const candidate of magic.results){
        const article=document.createElement('article');article.className='magic-result';
        const poster=candidate.poster_url?`<img src="${esc(candidate.poster_url)}" alt="" loading="lazy">`:'<div class="magic-poster-empty">?</div>';
        article.innerHTML=`${poster}<div><strong>${esc(candidate.title)}</strong>${candidate.year?` <span class="muted">(${esc(candidate.year)})</span>`:''}</div><div class="muted small">ID ${esc(candidate.external_id||candidate.id||'—')}</div><div class="magic-result-action"></div>`;
        const btn=document.createElement('button');btn.type='button';btn.textContent='Use this match';
        btn.addEventListener('click',()=>useMagicMatch(candidate,btn));
        article.querySelector('.magic-result-action').appendChild(btn);results.appendChild(article);
      }
    }catch(e){results.innerHTML=`<p class="error-text">${esc(e.message)}</p>`}
  }

  async function useMagicMatch(candidate,button){
    button.disabled=true;button.textContent='Applying…';
    try{
      const r=await fetch('/api/magic-intake/force-match',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({group_key:magic.groupKey,candidate})});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      const card=magicCards().find(x=>x.dataset.groupKey===magic.groupKey);
      if(card){
        const title=card.querySelector('[data-magic-title]');if(title)title.textContent=candidate.title||title.textContent;
        const year=card.querySelector('[data-magic-year]');if(year&&candidate.year)year.textContent=`(${candidate.year})`;
        const conf=card.querySelector('[data-magic-confidence]');if(conf){conf.textContent='100%';conf.classList.add('good');conf.classList.remove('warn','bad')}
        const state=card.querySelector('[data-magic-state]');if(state)state.textContent='matched';
        card.dataset.state='matched';
        const poster=card.querySelector('.magic-poster');
        if(candidate.poster_url&&poster)poster.innerHTML=`<img src="${esc(candidate.poster_url)}" alt="" loading="lazy">`;
        card.dataset.search=`${candidate.title||''} ${card.dataset.search||''}`.toLowerCase();
      }
      modal(false);applyMagicFilters();
    }catch(e){button.disabled=false;button.textContent='Use this match';alert(e.message)}
  }

  async function queueMagicImport(form){
    const data=new FormData(form);
    const button=form.querySelector('[data-magic-import-button]');
    if(button){button.disabled=true;button.textContent='Queuing…'}
    try{
      const payload={group_key:data.get('group_key')||'',destination_key:data.get('destination_key')||'',selected_source:data.get('selected_source')||''};
      const r=await fetch('/api/magic-intake/import',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify(payload)});
      const out=await r.json();if(!r.ok)throw new Error(out.detail||`HTTP ${r.status}`);
      const card=form.closest('.magic-card');
      if(card){
        card.dataset.state='importing';
        const state=card.querySelector('[data-magic-state]');if(state)state.textContent='queued';
        const wrap=card.querySelector('[data-magic-progress-wrap]');if(wrap)wrap.hidden=false;
        const text=card.querySelector('[data-magic-progress-text]');if(text)text.textContent=out.detail||'Import queued';
        const bar=card.querySelector('[data-magic-progress-bar]');if(bar)bar.style.width='2%';
      }
      if(button)button.textContent='Working…';
      applyMagicFilters();
    }catch(e){if(button){button.disabled=false;button.textContent='Import'}alert(e.message)}
  }

  function updateMagicCard(card,row){
    card.dataset.state=row.state||'';
    card.dataset.genres=(row.genres||[]).join('|').toLowerCase();
    card.dataset.themes=(row.themes||[]).join('|').toLowerCase();
    const state=card.querySelector('[data-magic-state]');if(state)state.textContent=row.state||'';
    const wrap=card.querySelector('[data-magic-progress-wrap]');
    if(wrap)wrap.hidden=!(row.progress||['queued','importing','verifying','partial','imported'].includes(row.state));
    const bar=card.querySelector('[data-magic-progress-bar]');if(bar)bar.style.width=`${Math.max(0,Math.min(100,Number(row.progress||0)))}%`;
    const text=card.querySelector('[data-magic-progress-text]');if(text)text.textContent=row.progress_detail||row.state||'';
    const err=card.querySelector('[data-magic-error]');if(err){err.textContent=row.error||'';err.hidden=!row.error}
    const button=card.querySelector('[data-magic-import-button]');
    if(button){
      const working=['queued','importing','verifying'].includes(row.state);button.disabled=working||row.state==='imported';
      if(row.state==='imported')button.textContent='Imported';else if(working)button.textContent='Working…';
    }
    card.classList.toggle('magic-card-imported',row.state==='imported');
  }

  function magicCardHtml(row,cfg){
    const year=row.match_year||row.year||'';
    const conf=Number(row.confidence||0);
    const threshold=Number((cfg||{}).auto_match_threshold||95);
    const confClass=conf>=threshold?'good':(conf>=80?'warn':'bad');
    const genres=(row.genres||[]).slice(0,6).map(x=>`<span class="tag">${esc(x)}</span>`).join('');
    const seasons=(row.seasons||[]);
    const seasonText=row.media_type==='tv'&&seasons.length?` · Season${seasons.length===1?'':'s'} ${seasons.join(', ')}`:'';
    const episodeText=row.media_type==='tv'&&row.episode_count?` · ${esc(row.episode_count)} episode marker(s)`:'';
    const releases=(row.source_paths||[]).map(x=>`<div>${esc(x)}</div>`).join('');
    const destinations=Object.entries(row.destination_options||{}).map(([key,path])=>`<option value="${esc(key)}" ${row.destination_key===key?'selected':''}>${esc(key.replace('plus','+').replace(/\b\w/g,c=>c.toUpperCase()))} · ${esc(path)}</option>`).join('');
    let sourceField='<input type="hidden" name="selected_source" value="">';
    if(row.media_type==='movie'&&(row.source_paths||[]).length>1){
      sourceField=`<label><span>Movie release</span><select name="selected_source" required>${(row.source_paths||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('')}</select></label>`;
    }
    const importLabel=row.media_type==='tv'?'Import Series':(row.media_type==='music'?'Import Artist':'Import');
    const working=['queued','importing','verifying'].includes(row.state);
    const importForm=row.match_title?`<form method="post" action="/magic-intake/import" class="magic-import-form" data-magic-import-form><input type="hidden" name="group_key" value="${esc(row.group_key)}"><label><span>Destination</span><select name="destination_key" required>${destinations}</select></label>${sourceField}<button type="submit" class="primary" data-magic-import-button ${working?'disabled':''}>${working?'Working…':importLabel}</button></form>`:'';
    const poster=row.poster_url?`<img src="${esc(row.poster_url)}" alt="" loading="lazy">`:'<div class="magic-poster-empty">?</div>';
    const progressVisible=Number(row.progress||0)||['queued','importing','verifying','partial'].includes(row.state);
    const searchText=`${row.display_title||''} ${(row.source_paths||[]).join(' ')}`.toLowerCase();
    return `<article class="magic-card" id="${esc(row.dom_id||'')}" data-group-key="${esc(row.group_key)}" data-media-type="${esc(row.media_type)}" data-state="${esc(row.state)}" data-genres="${esc((row.genres||[]).join('|').toLowerCase())}" data-themes="${esc((row.themes||[]).join('|').toLowerCase())}" data-search="${esc(searchText)}">
      <div class="magic-poster">${poster}</div><div class="magic-card-body">
      <div class="magic-card-title"><strong data-magic-title>${esc(row.display_title||row.match_title||row.normalized_title||'Unknown')}</strong>${year?` <span class="muted" data-magic-year>(${esc(year)})</span>`:''}</div>
      <div class="magic-tags"><span class="pill">${esc(String(row.media_type||'').toUpperCase())}</span><span class="pill ${confClass}" data-magic-confidence>${conf}%</span><span class="pill" data-magic-state>${esc(row.state||'')}</span></div>
      <p class="muted magic-summary"><strong>${esc(row.release_count||0)}</strong> release entr${Number(row.release_count||0)===1?'y':'ies'}${seasonText}${episodeText}</p>
      ${genres?`<div class="magic-genres">${genres}</div>`:''}
      <details class="magic-release-details"><summary>Source releases</summary><div class="mono small">${releases}</div></details>
      ${importForm}
      <div class="magic-progress" data-magic-progress-wrap ${progressVisible?'':'hidden'}><div class="magic-progress-track"><span data-magic-progress-bar style="width:${Math.max(0,Math.min(100,Number(row.progress||0)))}%"></span></div><div class="muted small" data-magic-progress-text>${esc(row.progress_detail||row.state||'')}</div></div>
      <div class="magic-actions"><button type="button" class="secondary" data-force-match data-group-key="${esc(row.group_key)}" data-media-type="${esc(row.media_type)}" data-title="${esc(row.normalized_title||row.display_title||'')}">Force Match</button><form method="post" action="/magic-intake/ignore"><input type="hidden" name="group_key" value="${esc(row.group_key)}"><button class="secondary" type="submit">Ignore</button></form></div>
      <p class="error-text" data-magic-error ${row.error?'':'hidden'}>${esc(row.error||'')}</p></div></article>`;
  }

  function syncMagicFilterOptions(filters){
    const update=(selector,values,allLabel)=>{
      const el=document.querySelector(selector);if(!el)return;
      const current=el.value;
      const wanted=['all',...(values||[]).map(x=>String(x).toLowerCase())];
      const existing=Array.from(el.options).map(x=>x.value.toLowerCase());
      if(wanted.join('|')===existing.join('|'))return;
      el.innerHTML=`<option value="all">${esc(allLabel)}</option>`+(values||[]).map(x=>`<option value="${esc(String(x).toLowerCase())}">${esc(x)}</option>`).join('');
      el.value=wanted.includes(current.toLowerCase())?current:'all';
    };
    update('#magic-filter-genre',(filters||{}).genres||[],'All genres');
    update('#magic-filter-theme',(filters||{}).themes||[],'All themes');
  }

  async function pollMagic(){
    if(!window.ArrNexusMagicIntake)return;
    try{
      const r=await fetch('/api/magic-intake',{cache:'no-store',headers:{'Accept':'application/json'}});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      for(const [key,value] of Object.entries(data.summary||{})){
        const el=document.querySelector(`[data-magic-stat="${key}"]`);if(el)el.textContent=value;
      }
      syncMagicFilterOptions(data.filters||{});
      const rows=new Map((data.groups||[]).map(x=>[x.group_key,x]));
      const existing=new Map(magicCards().map(x=>[x.dataset.groupKey,x]));
      for(const [key,row] of rows){
        let card=existing.get(key);
        if(!card&&row.state!=='imported'){
          const grid=document.querySelector('#magic-grid');
          if(grid){grid.insertAdjacentHTML('beforeend',magicCardHtml(row,data.settings||{}));card=magicCards().find(x=>x.dataset.groupKey===key)}
        }
        if(card)updateMagicCard(card,row);
      }
      for(const [key,card] of existing){if(!rows.has(key))card.classList.add('magic-stale')}
      applyMagicFilters();
    }catch(e){console.debug('magic poll:',e.message)}
    setTimeout(pollMagic,2000);
  }

  function initMagic(){
    if(!window.ArrNexusMagicIntake)return;
    document.querySelectorAll('[data-magic-type]').forEach(button=>button.addEventListener('click',()=>setMagicType(button.dataset.magicType||'all',button)));
    ['#magic-filter-search','#magic-filter-genre','#magic-filter-theme','#magic-filter-state'].forEach(selector=>{
      const el=document.querySelector(selector);if(el){el.addEventListener('input',applyMagicFilters);el.addEventListener('change',applyMagicFilters)}
    });
    document.querySelectorAll('[data-magic-modal-close]').forEach(x=>x.addEventListener('click',()=>modal(false)));
    document.querySelector('#magic-match-search')?.addEventListener('submit',e=>{e.preventDefault();searchMagicMatch()});
    document.addEventListener('click',e=>{
      const button=e.target.closest('[data-force-match]');
      if(!button)return;
      e.preventDefault();magic.groupKey=button.dataset.groupKey||'';magic.mediaType=button.dataset.mediaType||'movie';
      const q=document.querySelector('#magic-match-query');if(q)q.value=button.dataset.title||'';
      const results=document.querySelector('#magic-match-results');if(results)results.innerHTML='<p class="empty">Press Search to find the correct match.</p>';
      modal(true);
    });
    document.addEventListener('submit',e=>{
      const form=e.target.closest('[data-magic-import-form]');if(!form)return;
      e.preventDefault();queueMagicImport(form);
    });
    document.addEventListener('keydown',e=>{if(e.key==='Escape')modal(false)});
    applyMagicFilters();pollMagic();
  }


  document.addEventListener('click',e=>{const button=e.target.closest('[data-copy]');if(button){e.preventDefault();copyTarget(button)}});
  window.addEventListener('load',()=>{pollPipeline();initMagic()});
})();
