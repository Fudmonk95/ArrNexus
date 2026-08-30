(function(){
  const root=document.documentElement;
  // The server/profile owns the theme. A local override is only used by the
  // optional preview helper and never clobbers the user's saved profile theme.
  window.previewTheme=function(name){ root.dataset.theme=name; };
  window.toggleAll=function(master,name){
    document.querySelectorAll('input[name="'+name+'"]').forEach(cb=>cb.checked=master.checked);
    updateSelected();
  };
  window.updateSelected=function(){
    const n=document.querySelectorAll('input[name="source_path"]:checked').length;
    document.querySelectorAll('[data-selected-count]').forEach(el=>el.textContent=n);
  };
  document.addEventListener('change',e=>{if(e.target && e.target.name==='source_path') updateSelected();});
  document.addEventListener('click',e=>{
    const b=e.target.closest('[data-reveal]'); if(!b) return;
    const input=b.parentElement.querySelector('input'); if(!input) return;
    input.type=input.type==='password'?'text':'password'; b.textContent=input.type==='password'?'Show':'Hide';
  });
})();
