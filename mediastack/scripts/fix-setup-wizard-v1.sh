#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/arrnexus-mediastack-src}"
BRANCH="feature/mediastack-v1-control-plane"

[[ -d "$REPO_DIR/.git" ]] || { echo "ArrNexus checkout not found at $REPO_DIR" >&2; exit 2; }
cd "$REPO_DIR"

git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

python3 - <<'PY'
from pathlib import Path

path = Path("app/templates/stack_setup.html")
text = path.read_text(encoding="utf-8")

old = '''        <label>PUID<input type="number" min="1" max="65535" name="puid" value="{{ setup.state.puid }}"></label>
        <label>PGID<input type="number" min="1" max="65535" name="pgid" value="{{ setup.state.pgid }}"></label>
        <label>Jellyfin hardware acceleration<select name="jellyfin_gpu"><option value="auto" {% if setup.state.jellyfin_gpu == 'auto' %}selected{% endif %}>Auto-detect /dev/dri</option><option value="enabled" {% if setup.state.jellyfin_gpu == 'enabled' %}selected{% endif %}>Require GPU</option><option value="disabled" {% if setup.state.jellyfin_gpu == 'disabled' %}selected{% endif %}>CPU only</option></select></label>
'''
new = '''        <label>Default PUID for new LinuxServer services<input type="number" min="1" max="65535" name="puid" value="{{ setup.state.puid }}"><span class="field-help">Used only when ArrNexus installs a new LSIO-style service. Existing adopted containers keep their current Docker user.</span></label>
        <label>Default PGID for new LinuxServer services<input type="number" min="1" max="65535" name="pgid" value="{{ setup.state.pgid }}"><span class="field-help">Used only for new installs. It does not change the UID/GID of an adopted service.</span></label>
        <label>Jellyfin hardware acceleration<select name="jellyfin_gpu"><option value="auto" {% if setup.state.jellyfin_gpu == 'auto' %}selected{% endif %}>Auto-detect /dev/dri</option><option value="enabled" {% if setup.state.jellyfin_gpu == 'enabled' %}selected{% endif %}>Require GPU</option><option value="disabled" {% if setup.state.jellyfin_gpu == 'disabled' %}selected{% endif %}>CPU only</option></select><span class="field-help">Your existing Jellyfin identity is preserved separately from the LSIO defaults.</span></label>
'''
if old not in text:
    raise SystemExit("Could not find PUID/PGID wizard block")
text = text.replace(old, new, 1)

old = '''      <div class="setup-callout" style="margin-top:14px">Current-server adoption keeps Jellyfin's proven UID/GID/device access and Zurg mount propagation. Fresh install preflight refuses to apply a plan if blocking Docker, mount or port checks fail.</div>'''
new = '''      <div class="setup-callout" style="margin-top:14px"><strong>Existing identities are preserved.</strong> On this server Jellyfin is currently kept as <span class="mono">1001:1001</span> with video/render groups <span class="mono">44 / 104</span> and <span class="mono">/dev/dri</span>. The 1000/1000 fields above are defaults for newly installed LinuxServer containers, not a global MediaStack identity. Zurg mount propagation is also retained exactly as it is now.</div>'''
if old not in text:
    raise SystemExit("Could not find identity callout")
text = text.replace(old, new, 1)

old = '''        <div class="policy-card" data-policy-service="{{ item.key }}" {% if item.key not in setup.state.selected_services %}hidden{% endif %}><label>{{ item.name }}<select name="policy_{{ item.key }}"><option value="manual" {% if setup.policies.get(item.key) == 'manual' %}selected{% endif %}>Manual</option><option value="review" {% if setup.policies.get(item.key) == 'review' %}selected{% endif %}>Review</option><option value="automatic" {% if setup.policies.get(item.key) == 'automatic' %}selected{% endif %}>Automatic</option></select><span class="field-help">{{ item.notes or ('Update risk: ' ~ item.update_risk) }}</span></label></div>'''
new = '''        <div class="policy-card" data-policy-service="{{ item.key }}" data-policy-risk="{{ item.update_risk }}" data-policy-default="{{ item.update_policy }}" data-saved-policy="{{ setup.policies.get(item.key, '') }}" {% if item.key not in setup.state.selected_services %}hidden{% endif %}><label>{{ item.name }}<select name="policy_{{ item.key }}"><option value="manual" {% if setup.policies.get(item.key) == 'manual' %}selected{% endif %}>Manual</option><option value="review" {% if setup.policies.get(item.key) == 'review' %}selected{% endif %}>Review</option><option value="automatic" {% if setup.policies.get(item.key) == 'automatic' %}selected{% endif %}>Automatic</option></select><span class="field-help">{{ item.notes or ('Update risk: ' ~ item.update_risk) }}</span></label></div>'''
if old not in text:
    raise SystemExit("Could not find policy card block")
text = text.replace(old, new, 1)

old = '''  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const selectedServices=()=>[...form.querySelectorAll('input[name="services"]:checked')].map(el=>el.value);
  const syncPolicies=()=>{const selected=new Set(selectedServices());document.querySelectorAll('[data-policy-service]').forEach(el=>el.hidden=!selected.has(el.dataset.policyService));};
'''
new = '''  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const catalog={{ setup.catalog|tojson }};
  const catalogByKey=Object.fromEntries(catalog.map(item=>[item.key,item]));
  const selectedServices=()=>[...form.querySelectorAll('input[name="services"]:checked')].map(el=>el.value);
  const expandedServices=()=>{const selected=new Set(selectedServices()),queue=[...selected];while(queue.length){const key=queue.shift(),item=catalogByKey[key]||{};(item.dependencies||[]).forEach(dep=>{if(!selected.has(dep)){selected.add(dep);queue.push(dep);}});}return selected;};
  const globalMode=()=>form.querySelector('input[name="update_mode"]:checked')?.value||'review';
  const inheritedPolicy=(key,mode=globalMode())=>{const item=catalogByKey[key]||{};if(String(item.update_risk||'').toLowerCase()==='high')return 'manual';if(mode==='manual')return 'manual';if(mode==='review')return 'review';return String(item.update_policy||'manual').toLowerCase()==='automatic'?'automatic':'manual';};
  const syncPolicies=(applyDefaults=false)=>{const selected=expandedServices();document.querySelectorAll('[data-policy-service]').forEach(card=>{const visible=selected.has(card.dataset.policyService);card.hidden=!visible;if(!visible)return;const select=card.querySelector('select');if(!select)return;const hasSaved=Boolean(card.dataset.savedPolicy);if(applyDefaults&&!hasSaved&&!select.dataset.userTouched)select.value=inheritedPolicy(card.dataset.policyService);});};
'''
if old not in text:
    raise SystemExit("Could not find setup JavaScript policy helpers")
text = text.replace(old, new, 1)

old = '''  const show=n=>{current=Math.max(0,Math.min(steps.length-1,n));steps.forEach((el,i)=>el.hidden=i!==current);tabs.forEach((el,i)=>el.classList.toggle('active',i===current));if(current===2)preview(false);if(current===3)syncPolicies();if(current===4)preview(true);window.scrollTo({top:0,behavior:'smooth'});};
  document.querySelectorAll('[data-next]').forEach(b=>b.addEventListener('click',()=>show(current+1)));document.querySelectorAll('[data-prev]').forEach(b=>b.addEventListener('click',()=>show(current-1)));tabs.forEach((t,i)=>t.addEventListener('click',()=>show(i)));
  document.querySelectorAll('input[name="services"]').forEach(el=>el.addEventListener('change',syncPolicies));document.querySelectorAll('input[data-required="1"]').forEach(el=>{el.checked=true;el.addEventListener('click',e=>{e.preventDefault();el.checked=true;});});
  syncPolicies();show(0);
'''
new = '''  const show=n=>{current=Math.max(0,Math.min(steps.length-1,n));steps.forEach((el,i)=>el.hidden=i!==current);tabs.forEach((el,i)=>el.classList.toggle('active',i===current));if(current===2)preview(false);if(current===3)syncPolicies(true);if(current===4)preview(true);window.scrollTo({top:0,behavior:'smooth'});};
  document.querySelectorAll('[data-next]').forEach(b=>b.addEventListener('click',()=>show(current+1)));document.querySelectorAll('[data-prev]').forEach(b=>b.addEventListener('click',()=>show(current-1)));tabs.forEach((t,i)=>t.addEventListener('click',()=>show(i)));
  document.querySelectorAll('input[name="services"]').forEach(el=>el.addEventListener('change',()=>syncPolicies(true)));
  document.querySelectorAll('input[name="update_mode"]').forEach(el=>el.addEventListener('change',()=>{document.querySelectorAll('[data-policy-service]:not([hidden]) select').forEach(select=>{const card=select.closest('[data-policy-service]');select.value=inheritedPolicy(card.dataset.policyService);select.dataset.userTouched='';});syncPolicies(false);}));
  document.querySelectorAll('[data-policy-service] select').forEach(select=>select.addEventListener('change',()=>{select.dataset.userTouched='1';}));
  document.querySelectorAll('input[data-required="1"]').forEach(el=>{el.checked=true;el.addEventListener('click',e=>{e.preventDefault();el.checked=true;});});
  syncPolicies(false);show(0);
'''
if old not in text:
    raise SystemExit("Could not find setup JavaScript event block")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
PY

python3 - <<'PY'
from pathlib import Path
path=Path('app/static/stack_setup.css')
text=path.read_text(encoding='utf-8')
rule='.setup-step[data-step="3"]>.choice-grid{grid-template-columns:repeat(3,minmax(0,1fr))}'
if rule not in text:
    text += '\n'+rule+'@media(max-width:900px){.setup-step[data-step="3"]>.choice-grid{grid-template-columns:1fr}}\n'
path.write_text(text,encoding='utf-8')
PY

python3 -m py_compile app/stack_setup.py app/main.py
git diff --check

echo "Wizard fixes being committed:"
git diff --stat

git add app/templates/stack_setup.html app/static/stack_setup.css
git commit -m "Fix setup identity guidance and policy inheritance"
git push origin "$BRANCH"

echo
echo "MediaStack setup wizard fixes pushed to $BRANCH."
echo "No running Docker container was changed."
