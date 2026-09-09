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
  const magic={type:'all',groupKey:'',mediaType:'movie',results:[],offset:0,limit:72,total:0,revision:'',reloadTimer:null};
  const magicMovedStates=new Set(['awaiting_arr','partially_verified','numbering_mismatch','verification_timeout','source_missing','partial']);
  const magicWorkingStates=new Set(['queued','importing','verifying']);

  function magicCards(){return Array.from(document.querySelectorAll('#magic-grid .magic-card'))}

  function magicFilterValues(){
    return {
      media_type:magic.type||'all',
      q:(document.querySelector('#magic-filter-search')?.value||'').trim(),
      genre:document.querySelector('#magic-filter-genre')?.value||'all',
      theme:document.querySelector('#magic-filter-theme')?.value||'all',
      state:document.querySelector('#magic-filter-state')?.value||'all',
    };
  }

  function updateMagicCounts(total){
    magic.total=Number(total||0);
    const count=document.querySelector('#magic-visible-count');if(count)count.textContent=magic.total;
    const loaded=document.querySelector('#magic-loaded-count');if(loaded)loaded.textContent=magicCards().length;
    const empty=document.querySelector('#magic-filter-empty');if(empty)empty.hidden=magic.total!==0;
    const more=document.querySelector('#magic-load-more');if(more)more.hidden=magicCards().length>=magic.total;
  }

  function applyMagicFilters(){
    // v13.1.2 filters are server-side/paginated. Keep this function as a
    // lightweight counter/update hook for existing callers.
    updateMagicCounts(magic.total||Number(window.ArrNexusMagicTotal||0));
  }

  function setMagicType(type,button){
    magic.type=type;
    document.querySelectorAll('[data-magic-type]').forEach(x=>x.classList.toggle('active',x===button));
    scheduleMagicReload(0);
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
      modal(false);scheduleMagicReload(900);
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
      updateMagicCounts(magic.total);
    }catch(e){if(button){button.disabled=false;button.textContent='Import'}alert(e.message)}
  }

  function ensureMagicRecheckButton(card,row){
    const actions=card.querySelector('.magic-actions');if(!actions)return;
    let button=actions.querySelector('[data-magic-recheck]');
    if(row.recheckable){
      if(!button){
        button=document.createElement('button');button.type='button';button.className='secondary';button.setAttribute('data-magic-recheck','');
        actions.insertBefore(button,actions.firstChild);
      }
      button.dataset.groupKey=row.group_key||card.dataset.groupKey||'';button.textContent='Recheck now';button.disabled=false;
    }else if(button){button.remove()}
  }

  function updateMagicCard(card,row){
    card.dataset.state=row.state||'';
    card.dataset.genres=(row.genres||[]).join('|').toLowerCase();
    card.dataset.themes=(row.themes||[]).join('|').toLowerCase();
    const state=card.querySelector('[data-magic-state]');if(state)state.textContent=row.status_label||row.state||'';
    const wrap=card.querySelector('[data-magic-progress-wrap]');
    if(wrap)wrap.hidden=!(row.progress||magicWorkingStates.has(row.state)||magicMovedStates.has(row.state)||['failed','imported'].includes(row.state));
    const bar=card.querySelector('[data-magic-progress-bar]');if(bar)bar.style.width=`${Math.max(0,Math.min(100,Number(row.progress||0)))}%`;
    const text=card.querySelector('[data-magic-progress-text]');if(text)text.textContent=row.progress_detail||row.status_label||row.state||'';
    const err=card.querySelector('[data-magic-error]');if(err){err.textContent=row.error||'';err.hidden=!row.error}
    const button=card.querySelector('[data-magic-import-button]');
    if(button){
      const working=magicWorkingStates.has(row.state);
      const moved=magicMovedStates.has(row.state);
      button.disabled=working||moved||row.state==='imported';
      if(row.state==='imported')button.textContent='Imported';
      else if(working)button.textContent='Working…';
      else if(moved)button.textContent='Moved — recheck';
      else if(row.state==='failed')button.textContent='Retry Import';
    }
    ensureMagicRecheckButton(card,row);
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
    const working=magicWorkingStates.has(row.state);
    const moved=magicMovedStates.has(row.state);
    let buttonLabel=working?'Working…':(moved?'Moved — recheck':(row.state==='failed'?'Retry Import':importLabel));
    const importForm=row.match_title?`<form method="post" action="/magic-intake/import" class="magic-import-form" data-magic-import-form><input type="hidden" name="group_key" value="${esc(row.group_key)}"><label><span>Destination</span><select name="destination_key" required>${destinations}</select></label>${sourceField}<button type="submit" class="primary" data-magic-import-button ${(working||moved)?'disabled':''}>${esc(buttonLabel)}</button></form>`:'';
    const poster=row.poster_url?`<img src="${esc(row.poster_url)}" alt="" loading="lazy">`:'<div class="magic-poster-empty">?</div>';
    const progressVisible=Number(row.progress||0)||working||moved||['failed'].includes(row.state);
    const searchText=`${row.display_title||''} ${(row.source_paths||[]).join(' ')}`.toLowerCase();
    const recheck=row.recheckable?`<button type="button" class="secondary" data-magic-recheck data-group-key="${esc(row.group_key)}">Recheck now</button>`:'';
    return `<article class="magic-card" id="${esc(row.dom_id||'')}" data-group-key="${esc(row.group_key)}" data-media-type="${esc(row.media_type)}" data-state="${esc(row.state)}" data-genres="${esc((row.genres||[]).join('|').toLowerCase())}" data-themes="${esc((row.themes||[]).join('|').toLowerCase())}" data-search="${esc(searchText)}">
      <div class="magic-poster">${poster}</div><div class="magic-card-body">
      <div class="magic-card-title"><strong data-magic-title>${esc(row.display_title||row.match_title||row.normalized_title||'Unknown')}</strong>${year?` <span class="muted" data-magic-year>(${esc(year)})</span>`:''}</div>
      <div class="magic-tags"><span class="pill">${esc(String(row.media_type||'').toUpperCase())}</span><span class="pill ${confClass}" data-magic-confidence>${conf}%</span><span class="pill" data-magic-state>${esc(row.status_label||row.state||'')}</span></div>
      <p class="muted magic-summary"><strong>${esc(row.release_count||0)}</strong> release entr${Number(row.release_count||0)===1?'y':'ies'}${seasonText}${episodeText}</p>
      ${genres?`<div class="magic-genres">${genres}</div>`:''}
      <details class="magic-release-details"><summary>Source releases</summary><div class="mono small">${releases}</div></details>
      <label class="magic-type-control"><span>Media type</span><select data-magic-type-override data-group-key="${esc(row.group_key)}" ${[...magicWorkingStates,...magicMovedStates,'imported'].includes(row.state)?'disabled title="Media type is locked after the move stage"':''}><option value="movie" ${row.media_type==='movie'?'selected':''}>Movie</option><option value="tv" ${row.media_type==='tv'?'selected':''}>TV Series</option><option value="music" ${row.media_type==='music'?'selected':''}>Music</option></select></label>
      ${importForm}
      <div class="magic-progress" data-magic-progress-wrap ${progressVisible?'':'hidden'}><div class="magic-progress-track"><span data-magic-progress-bar style="width:${Math.max(0,Math.min(100,Number(row.progress||0)))}%"></span></div><div class="muted small" data-magic-progress-text>${esc(row.progress_detail||row.status_label||row.state||'')}</div></div>
      <div class="magic-actions">${recheck}<button type="button" class="secondary" data-force-match data-group-key="${esc(row.group_key)}" data-media-type="${esc(row.media_type)}" data-title="${esc(row.normalized_title||row.display_title||'')}">Force Match</button><form method="post" action="/magic-intake/ignore"><input type="hidden" name="group_key" value="${esc(row.group_key)}"><button class="secondary" type="submit">Ignore</button></form></div>
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

  async function changeMagicType(select){
    const groupKey=select.dataset.groupKey||'';
    const mediaType=select.value||'';
    if(!groupKey||!['movie','tv','music'].includes(mediaType))return;
    const before=select.dataset.previous||select.querySelector('option[selected]')?.value||'';
    select.disabled=true;
    try{
      const r=await fetch('/api/magic-intake/type',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({group_key:groupKey,media_type:mediaType})});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      scheduleMagicReload(250);
    }catch(e){
      if(before)select.value=before;
      select.disabled=false;
      alert(e.message);
    }
  }

  async function requestMagicRecheck(button){
    const groupKey=button.dataset.groupKey||'';if(!groupKey)return;
    const before=button.textContent;button.disabled=true;button.textContent='Rechecking…';
    try{
      const r=await fetch('/api/magic-intake/recheck',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({group_key:groupKey})});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      button.textContent=data.queued?'Rechecking…':'Recheck queued';
      setTimeout(()=>{if(button.isConnected){button.disabled=false;button.textContent='Recheck now'}},2500);
    }catch(e){button.disabled=false;button.textContent=before;alert(e.message)}
  }

  async function fetchMagicGroups({append=false}={}){
    if(!window.ArrNexusMagicIntake)return;
    const grid=document.querySelector('#magic-grid');if(!grid)return;
    const filters=magicFilterValues();
    const offset=append?magicCards().length:0;
    const params=new URLSearchParams({...filters,offset:String(offset),limit:String(magic.limit)});
    try{
      const r=await fetch(`/api/magic-intake/groups?${params.toString()}`,{cache:'no-store',headers:{'Accept':'application/json'}});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);
      magic.revision=data.revision||magic.revision;
      if(!append)grid.innerHTML='';
      for(const row of data.rows||[])grid.insertAdjacentHTML('beforeend',magicCardHtml(row,data.settings||{}));
      syncMagicFilterOptions(data.filters||{});
      updateMagicCounts(data.total||0);
    }catch(e){
      console.debug('magic groups:',e.message);
      const empty=document.querySelector('#magic-filter-empty');if(empty){empty.hidden=false;empty.textContent=`Could not load intake: ${e.message}`}
    }
  }

  function scheduleMagicReload(delay=180){
    if(magic.reloadTimer)clearTimeout(magic.reloadTimer);
    magic.reloadTimer=setTimeout(()=>{magic.reloadTimer=null;fetchMagicGroups({append:false})},delay);
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
      const existing=new Map(magicCards().map(x=>[x.dataset.groupKey,x]));
      for(const row of data.updates||[]){
        const card=existing.get(row.group_key);
        if(card){
          updateMagicCard(card,row);
          if(row.state==='imported')card.remove();
        }
      }
      if(data.revision&&magic.revision&&data.revision!==magic.revision){
        magic.revision=data.revision;scheduleMagicReload(80);
      }else if(data.revision&&!magic.revision){magic.revision=data.revision}
      updateMagicCounts(magic.total||Number(window.ArrNexusMagicTotal||0));
    }catch(e){console.debug('magic poll:',e.message)}
    setTimeout(pollMagic,3000);
  }

  function initMagic(){
    if(!window.ArrNexusMagicIntake)return;
    magic.total=Number(window.ArrNexusMagicTotal||0);
    magic.limit=Number(window.ArrNexusMagicLimit||72);
    magic.revision=String(window.ArrNexusMagicRevision||'');
    document.querySelectorAll('[data-magic-type]').forEach(button=>button.addEventListener('click',()=>setMagicType(button.dataset.magicType||'all',button)));
    const search=document.querySelector('#magic-filter-search');if(search)search.addEventListener('input',()=>scheduleMagicReload(250));
    ['#magic-filter-genre','#magic-filter-theme','#magic-filter-state'].forEach(selector=>{
      const el=document.querySelector(selector);if(el)el.addEventListener('change',()=>scheduleMagicReload(0));
    });
    document.querySelector('#magic-load-more')?.addEventListener('click',()=>fetchMagicGroups({append:true}));
    document.addEventListener('focusin',e=>{const sel=e.target.closest?.('[data-magic-type-override]');if(sel)sel.dataset.previous=sel.value});
    document.addEventListener('change',e=>{const sel=e.target.closest?.('[data-magic-type-override]');if(sel){changeMagicType(sel)}});
    document.querySelectorAll('[data-magic-modal-close]').forEach(x=>x.addEventListener('click',()=>modal(false)));
    document.querySelector('#magic-match-search')?.addEventListener('submit',e=>{e.preventDefault();searchMagicMatch()});
    document.addEventListener('click',e=>{
      const recheck=e.target.closest('[data-magic-recheck]');
      if(recheck){e.preventDefault();requestMagicRecheck(recheck);return}
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
    updateMagicCounts(magic.total);pollMagic();
  }


  document.addEventListener('click',e=>{const button=e.target.closest('[data-copy]');if(button){e.preventDefault();copyTarget(button)}});
  window.addEventListener('load',()=>{pollPipeline();initMagic()});
})();
