(function(){
  const root=document.documentElement;
  const saved=localStorage.getItem('theme')||'dark';
  root.dataset.theme=saved;
  window.toggleTheme=function(){
    const next=root.dataset.theme==='light'?'dark':'light';
    root.dataset.theme=next; localStorage.setItem('theme',next);
  };
  window.toggleAll=function(master,name){
    document.querySelectorAll('input[name="'+name+'"]').forEach(cb=>cb.checked=master.checked);
    updateSelected();
  };
  window.updateSelected=function(){
    const n=document.querySelectorAll('input[name="source_path"]:checked').length;
    document.querySelectorAll('[data-selected-count]').forEach(el=>el.textContent=n);
  };
  document.addEventListener('change',e=>{if(e.target && e.target.name==='source_path') updateSelected();});
})();
