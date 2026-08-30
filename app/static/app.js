(function(){
  const root=document.documentElement;
  window.previewTheme=name=>{ root.dataset.theme=name; };
  window.toggleAll=function(master,name){document.querySelectorAll('input[name="'+name+'"]').forEach(cb=>cb.checked=master.checked);updateSelected();};
  window.updateSelected=function(){const n=document.querySelectorAll('input[name="source_path"]:checked').length;document.querySelectorAll('[data-selected-count]').forEach(el=>el.textContent=n);};
  function toast(html,href){const stack=document.getElementById('jobToastStack');if(!stack)return;const a=document.createElement(href?'a':'div');a.className='job-toast';if(href)a.href=href;a.innerHTML=html;stack.prepend(a);return a;}
  async function pollJob(id,node){try{const r=await fetch('/api/jobs/'+id,{headers:{Accept:'application/json'}});if(!r.ok)return;const d=await r.json();const j=d.job||{};const done=(j.completed||0)+(j.failed||0),total=j.total||1,p=Math.round(done/total*100);node.innerHTML='<strong>Import job #'+id+' · '+(j.status||'running').replaceAll('_',' ')+'</strong><small>'+(j.completed||0)+' complete · '+(j.failed||0)+' failed · '+(j.message||'')+'</small><div class="job-progress"><i style="width:'+p+'%"></i></div>';if(['queued','running'].includes(j.status))setTimeout(()=>pollJob(id,node),1500);}catch(e){}}
  async function pollActive(){try{const r=await fetch('/api/jobs-active');if(!r.ok)return;const d=await r.json();(d.jobs||[]).forEach(j=>{if(document.querySelector('[data-toast-job="'+j.id+'"]'))return;const n=toast('<strong>Import job #'+j.id+'</strong><small>'+j.message+'</small><div class="job-progress"><i></i></div>','/jobs/'+j.id);n.dataset.toastJob=j.id;pollJob(j.id,n);});}catch(e){}}
  document.addEventListener('change',e=>{if(e.target?.name==='source_path')updateSelected();});
  document.addEventListener('click',e=>{
    const menu=e.target.closest('[data-mobile-menu]');if(menu){document.getElementById('appSidebar')?.classList.toggle('open');return;}
    const update=e.target.closest('[data-check-update]');if(update){update.disabled=true;update.textContent='Checking…';fetch('/api/update-check',{headers:{Accept:'application/json'}}).then(r=>r.json()).then(d=>{const el=document.getElementById('updateResult');if(el){el.textContent=d.error?'Update check failed: '+d.error:(!d.configured?'Set a GitHub repository first':(d.update_available?'Update available: '+d.latest:'Up to date · '+(d.latest||d.current)));}}).catch(err=>{const el=document.getElementById('updateResult');if(el)el.textContent='Update check failed: '+err.message;}).finally(()=>{update.disabled=false;update.textContent='Check now';});return;}
    const b=e.target.closest('[data-reveal]');if(b){const input=b.parentElement.querySelector('input');if(input){input.type=input.type==='password'?'text':'password';b.textContent=input.type==='password'?'Show':'Hide';}return;}
    const theme=e.target.closest('[data-theme-choice]');if(theme){document.querySelectorAll('[data-theme-choice]').forEach(x=>x.classList.remove('selected'));theme.classList.add('selected');const name=theme.dataset.themeChoice;previewTheme(name);const input=document.getElementById('themeInput');if(input)input.value=name;return;}
    const arrow=e.target.closest('[data-scroll-shelf]');if(arrow){const shelf=document.getElementById('shelf-'+arrow.dataset.scrollShelf);if(shelf)shelf.scrollBy({left:(Number(arrow.dataset.dir)||1)*Math.max(500,shelf.clientWidth*.8),behavior:'smooth'});}
  });
  document.addEventListener('submit',async e=>{
    const form=e.target;if(form.id!=='bulkForm' && form.action && !form.action.endsWith('/import'))return;
    if(form.id!=='bulkForm' && !form.matches('[data-ajax-import]'))return;
    e.preventDefault();
    if(form.id==='bulkForm' && !form.querySelector('input[name="source_path"]:checked')){toast('<strong>Nothing selected</strong><small>Select at least one DMM item first.</small>');return;}
    try{const r=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'ArrNexus',Accept:'application/json'}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.detail||'Import could not start');const n=toast('<strong>Import job #'+d.job_id+' started</strong><small>'+d.total+' item(s) queued. Click for details.</small><div class="job-progress"><i style="width:3%"></i></div>',d.url);n.dataset.toastJob=d.job_id;pollJob(d.job_id,n);}catch(err){toast('<strong>Import failed to start</strong><small>'+err.message+'</small>');}
  });
  document.addEventListener('DOMContentLoaded',()=>{updateSelected();pollActive();setInterval(pollActive,5000);if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});});
})();
