(function(){
  const esc=(v)=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  async function pollPipeline(){
    if(!window.ArrNexusPipeline)return;
    try{
      const r=await fetch('/api/pipeline/live',{headers:{'Accept':'application/json'},cache:'no-store'});
      const data=await r.json();
      if(!data.ok)throw new Error(data.error||'Pipeline unavailable');
      for(const [k,v] of Object.entries(data.summary||{})){
        const el=document.querySelector(`[data-stat="${k}"]`);if(el)el.textContent=v;
      }
      const body=document.querySelector('#pipeline-table tbody');
      if(body){
        body.innerHTML=(data.rows||[]).map(x=>`<tr><td><strong>${esc(x.title)}</strong>${x.year?`<div class="muted">${esc(x.year)}</div>`:''}</td><td>${esc(x.requested_by||'—')}</td><td>${esc(x.service||'—')}</td><td><span class="stage stage-${esc(x.stage)}">${esc(x.stage_label)}</span></td><td>${esc(x.detail)}</td><td class="mono small">${esc(x.zurg_path||'—')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No recent Seerr requests.</td></tr>';
      }
    }catch(e){console.debug('pipeline poll:',e.message)}
    setTimeout(pollPipeline,2000);
  }
  window.addEventListener('load',pollPipeline);
})();
