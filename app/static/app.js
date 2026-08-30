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
    const logLine=e.target.closest('[data-log-line]');if(logLine){logLine.classList.toggle('open');return;}
    const liveBtn=e.target.closest('[data-live-logs]');if(liveBtn){liveBtn.dataset.paused=liveBtn.dataset.paused==='1'?'0':'1';liveBtn.textContent=liveBtn.dataset.paused==='1'?'Live: off':'Live: on';return;}
  });
  document.addEventListener('submit',async e=>{
    const form=e.target;if(form.id!=='bulkForm' && form.action && !form.action.endsWith('/import'))return;
    if(form.id!=='bulkForm' && !form.matches('[data-ajax-import]'))return;
    e.preventDefault();
    if(form.id==='bulkForm' && !form.querySelector('input[name="source_path"]:checked')){toast('<strong>Nothing selected</strong><small>Select at least one DMM item first.</small>');return;}
    try{const r=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'ArrNexus',Accept:'application/json'}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.detail||'Import could not start');const n=toast('<strong>Import job #'+d.job_id+' started</strong><small>'+d.total+' item(s) queued. Click for details.</small><div class="job-progress"><i style="width:3%"></i></div>',d.url);n.dataset.toastJob=d.job_id;pollJob(d.job_id,n);}catch(err){toast('<strong>Import failed to start</strong><small>'+err.message+'</small>');}
  });
  function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
  async function refreshExternalLogs(){const stream=document.getElementById('unifiedLogStream');if(!stream)return;const origin=stream.dataset.logOrigin;if(!['dumb','infinidysk'].includes(origin))return;const btn=document.querySelector('[data-live-logs]');if(btn?.dataset.paused==='1')return;try{const u=new URL('/api/logs/external',location.origin);u.searchParams.set('origin',origin);u.searchParams.set('process',stream.dataset.logProcess||'DUMB');u.searchParams.set('level',stream.dataset.logLevel||'all');u.searchParams.set('q',stream.dataset.logQuery||'');const r=await fetch(u,{headers:{Accept:'application/json'}});if(!r.ok)return;const d=await r.json();if(d.error)return;stream.innerHTML=(d.rows||[]).map(x=>{const diag=x.diagnostic?'<div class="log-diagnostic"><strong>'+esc(x.diagnostic.title)+'</strong><div>'+esc(x.diagnostic.explanation)+'</div><ul>'+((x.diagnostic.actions||[]).map(a=>'<li>'+esc(a)+'</li>').join(''))+'</ul></div>':'';return '<div class="log-line" data-level="'+esc(x.level)+'" data-log-line><span class="log-time">'+esc(x.created_at)+'</span><span class="log-level">'+esc(x.level)+'</span><span class="log-source">'+esc(x.source)+'</span><span class="log-message">'+esc(x.message)+'</span>'+diag+'</div>';}).join('')||'<div class="log-empty">No matching logs.</div>';}catch(e){}}
  document.addEventListener('DOMContentLoaded',()=>{updateSelected();pollActive();setInterval(pollActive,5000);setInterval(refreshExternalLogs,3500);if('serviceWorker' in navigator)navigator.serviceWorker.register('/static/sw.js').catch(()=>{});
    const badge=document.querySelector('.nx-version-badge[data-update-check="1"]');
    if(badge){try{const key='arrnexus:update-status';const cached=JSON.parse(localStorage.getItem(key)||'null');const fresh=cached&&Date.now()-(cached.at||0)<21600000;const apply=d=>{const em=badge.querySelector('[data-version-update]');if(em&&d?.update_available){em.hidden=false;em.textContent='UPDATE '+(d.latest||'');badge.classList.add('has-update');badge.title='ArrNexus update available: '+(d.latest||'new release');}};if(fresh)apply(cached.data);else fetch('/api/update-check',{headers:{Accept:'application/json'}}).then(r=>r.ok?r.json():null).then(d=>{if(d){localStorage.setItem(key,JSON.stringify({at:Date.now(),data:d}));apply(d);}}).catch(()=>{});}catch(_){}}
  });
})();

/* ArrNexus 7: fast soft navigation + command palette. */
(function(){
  const pageCache=new Map();
  const CACHE_MS=18000;
  const progress=()=>document.getElementById('nxProgress');
  function startProgress(){const p=progress();if(!p)return;p.classList.remove('done');void p.offsetWidth;p.classList.add('loading');}
  function finishProgress(){const p=progress();if(!p)return;p.classList.remove('loading');p.classList.add('done');setTimeout(()=>p.classList.remove('done'),420);}
  function canSoftNavigate(a){
    if(!a||a.target==='_blank'||a.hasAttribute('download')||a.dataset.noSoftNav!==undefined)return false;
    const href=a.getAttribute('href')||'';
    if(!href||href.startsWith('#')||href.startsWith('mailto:')||href.startsWith('javascript:'))return false;
    let u;try{u=new URL(href,location.href);}catch(_){return false;}
    if(u.origin!==location.origin)return false;
    if(['/logout','/settings/export-config','/diagnostics/download'].some(x=>u.pathname.startsWith(x)))return false;
    if(u.pathname.startsWith('/browser/file')||u.pathname.startsWith('/settings/backup/'))return false;
    return true;
  }
  async function getPage(url,{prefetch=false}={}){
    const key=new URL(url,location.href).href;
    const cached=pageCache.get(key);
    if(cached&&Date.now()-cached.at<CACHE_MS)return cached.html;
    const r=await fetch(key,{headers:{'X-ArrNexus-Navigation':'1','Accept':'text/html'},credentials:'same-origin'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const html=await r.text();
    pageCache.set(key,{html,at:Date.now()});
    if(pageCache.size>24){const first=pageCache.keys().next().value;pageCache.delete(first);}
    return html;
  }
  function applyPage(html,url,push=true){
    const parser=new DOMParser(),doc=parser.parseFromString(html,'text/html');
    const incoming=doc.getElementById('pageContent');
    const current=document.getElementById('pageContent');
    if(!incoming||!current){location.href=url;return;}
    current.innerHTML=incoming.innerHTML;
    current.classList.remove('nx-loading');current.classList.add('nx-loaded');setTimeout(()=>current.classList.remove('nx-loaded'),160);
    const newHeading=doc.querySelector('.nx-title-wrap h1');const heading=document.querySelector('.nx-title-wrap h1');if(newHeading&&heading)heading.textContent=newHeading.textContent;
    const newKicker=doc.querySelector('.nx-kicker');const kicker=document.querySelector('.nx-kicker');if(newKicker&&kicker)kicker.textContent=newKicker.textContent;
    if(doc.title)document.title=doc.title;
    const targetPath=new URL(url,location.href).pathname;
    document.querySelectorAll('.nx-nav-links>a').forEach(a=>{const p=new URL(a.href,location.href).pathname;let active=p==='/'?targetPath==='/':targetPath.startsWith(p);a.classList.toggle('active',active);});
    document.getElementById('appSidebar')?.classList.remove('open');
    if(push)history.pushState({arrnexus:true},'',url);
    window.scrollTo({top:0,behavior:'instant'});
    if(typeof window.updateSelected==='function')window.updateSelected();
    document.dispatchEvent(new CustomEvent('arrnexus:navigated',{detail:{url}}));
  }
  async function navigate(url,push=true){
    const main=document.getElementById('pageContent');if(!main){location.href=url;return;}
    main.classList.add('nx-loading');startProgress();
    try{const html=await getPage(url);applyPage(html,url,push);}catch(err){console.warn('Soft navigation fallback',err);location.href=url;return;}finally{finishProgress();}
  }
  document.addEventListener('click',e=>{
    const a=e.target.closest('a');if(!canSoftNavigate(a)||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey||e.button!==0)return;
    e.preventDefault();navigate(a.href,true);
  },true);
  let hoverTimer=null;
  document.addEventListener('pointerover',e=>{const a=e.target.closest('a');if(!canSoftNavigate(a))return;clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>getPage(a.href,{prefetch:true}).catch(()=>{}),90);});
  window.addEventListener('popstate',()=>navigate(location.href,false));

  const commands=()=>Array.from(document.querySelectorAll('.nx-nav-links>a')).map(a=>({label:(a.querySelector('span:last-child')?.textContent||a.textContent).trim(),href:a.href,group:a.closest('.nx-nav-section')?.querySelector('summary span')?.textContent||'Page',icon:a.querySelector('.nx-nav-icon')?.textContent||'•'}));
  const modal=()=>document.getElementById('nxCommand'),input=()=>document.getElementById('nxCommandInput'),results=()=>document.getElementById('nxCommandResults');
  function renderCommands(q=''){
    const term=q.trim().toLowerCase();let rows=commands().filter(x=>!term||x.label.toLowerCase().includes(term)||x.group.toLowerCase().includes(term));
    results().innerHTML=rows.map((x,i)=>'<a class="nx-command-result '+(i===0?'selected':'')+'" href="'+x.href+'"><span class="nx-nav-icon">'+x.icon+'</span><span><strong>'+x.label+'</strong><small>'+x.group+'</small></span><span>↵</span></a>').join('')||'<div class="log-empty">No matching page.</div>';
  }
  function openCommand(){const m=modal();if(!m)return;m.hidden=false;renderCommands('');setTimeout(()=>input()?.focus(),0);}
  function closeCommand(){const m=modal();if(m)m.hidden=true;}
  document.addEventListener('click',e=>{if(e.target.closest('[data-command-open]')){openCommand();return}if(e.target.closest('[data-command-close]')){closeCommand();return}});
  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();modal()?.hidden?openCommand():closeCommand();return;}
    if(e.key==='Escape'&&!modal()?.hidden){closeCommand();return;}
    if(!modal()?.hidden&&e.key==='Enter'){const a=results()?.querySelector('.nx-command-result.selected')||results()?.querySelector('.nx-command-result');if(a){e.preventDefault();closeCommand();navigate(a.href,true);}}
  });
  document.addEventListener('input',e=>{if(e.target?.id==='nxCommandInput')renderCommands(e.target.value)});
})();

/* ArrNexus 7.0: native InfiniDysk live overview + passive update badge. */
(function(){
  let infiniBusy=false;
  function humanBytes(n){n=Number(n||0);const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return (i===0?Math.round(n):n.toFixed(1))+' '+units[i];}
  function graphPoints(rows){const vals=(rows||[]).map(x=>Math.max(0,Number(x.bytesFetched||x.bytesServed||0)));const peak=Math.max(0,...vals);if(!peak||!vals.length)return '';return vals.map((v,i)=>{const x=vals.length<=1?0:(i/(vals.length-1))*100;const y=96-(v/peak)*88;return x.toFixed(2)+','+y.toFixed(2)}).join(' ');}
  async function refreshInfini(){const root=document.querySelector('[data-infinidysk-live]');if(!root||document.hidden||infiniBusy)return;infiniBusy=true;try{const w=root.dataset.window||'24h';const r=await fetch('/api/infinidysk/live?window='+encodeURIComponent(w),{headers:{Accept:'application/json'},credentials:'same-origin'});if(!r.ok)return;const d=await r.json();if(!d.ok)return;const t=d.overview?.tiles||{};root.querySelectorAll('[data-infini-stat]').forEach(el=>{const k=el.dataset.infiniStat,v=Number(t[k]||0);el.textContent=el.dataset.format==='bytes'?humanBytes(v):el.dataset.format==='rate'?humanBytes(v/60)+'/s':String(v);});const pts=graphPoints(d.overview?.throughput||[]);const line=root.querySelector('[data-infini-graph]'),area=root.querySelector('[data-infini-area]');if(line&&pts)line.setAttribute('points',pts);if(area&&pts)area.setAttribute('points','0,100 '+pts+' 100,100');const count=root.querySelector('[data-infini-queue-count]');const slots=d.queue?.slots||[];if(count)count.textContent=slots.length+' active queue item(s)';}catch(_e){}finally{infiniBusy=false;}}
  async function checkVersionBadge(){const badge=document.querySelector('.nx-version-badge');if(!badge||badge.dataset.checked==='1')return;badge.dataset.checked='1';try{const r=await fetch('/api/update-check',{headers:{Accept:'application/json'},credentials:'same-origin'});if(!(r.headers.get('content-type')||'').includes('json'))return;const d=await r.json();if(d.update_available){badge.classList.add('update');const small=badge.querySelector('small');if(small){small.textContent='UPDATE';small.title='Latest: '+(d.latest||'new release')}}}catch(_e){}}
  function activate(){refreshInfini();checkVersionBadge();}
  document.addEventListener('DOMContentLoaded',activate);document.addEventListener('arrnexus:navigated',activate);setInterval(refreshInfini,4000);
})();
