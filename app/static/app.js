(function(){
  const esc=(v)=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const terminal=new Set(['available','complete','failed','declined']);

  function lifecycle(x){
    const rows=(x.milestones||[]);
    if(!rows.length)return '<span class="muted">—</span>';
    return `<div class="milestones">${rows.map(step=>`<span class="milestone stage-${esc(step.stage)}" title="${esc(step.detail||'')}">${esc(step.label||step.stage)}</span>`).join('')}</div>`;
  }

  function rowHtml(x){
    return `<tr data-stage="${esc(x.stage)}">
      <td><strong>${esc(x.title)}</strong>${x.year?`<div class="muted">${esc(x.year)}</div>`:''}</td>
      <td>${esc(x.requested_by||'—')}</td>
      <td>${esc(x.source||'—')}</td>
      <td>${esc(x.service||'—')}</td>
      <td>${lifecycle(x)}</td>
      <td><span class="stage stage-${esc(x.stage)}">${esc(x.stage_label||x.stage)}</span></td>
      <td>${esc(x.detail||'')}</td>
      <td class="mono small path-cell">${esc(x.zurg_path||'—')}</td>
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
      if(body){
        body.innerHTML=(data.rows||[]).map(rowHtml).join('')||'<tr><td colspan="8" class="empty">Tracker is warming up or there is no current activity.</td></tr>';
      }
      const updated=document.querySelector('#pipeline-updated');
      if(updated)updated.textContent=data.updated_at||'warming up';
      const live=document.querySelector('#pipeline-live-state');
      if(live){
        live.textContent=data.stale?'STALE':'LIVE';
        live.classList.toggle('stale',!!data.stale);
      }
    }catch(e){
      const live=document.querySelector('#pipeline-live-state');
      if(live){live.textContent='DELAYED';live.classList.add('stale')}
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
      const before=button.textContent;
      button.textContent='Copied';
      setTimeout(()=>button.textContent=before,1300);
    }catch(_){
      const range=document.createRange();range.selectNodeContents(target);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
    }
  }

  document.addEventListener('click',e=>{
    const button=e.target.closest('[data-copy]');
    if(button){e.preventDefault();copyTarget(button)}
  });
  window.addEventListener('load',pollPipeline);
})();
