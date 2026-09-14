(() => {
  const form = document.getElementById('stack-setup-form');
  if (!form) return;

  const dependencyMap = {
    lidarr: ['lidarr-postgres'],
    profilarr: ['profilarr-parser'],
  };
  const blockedDefaults = new Set(['zurg', 'jellyfin', 'lidarr-postgres']);
  const catalogDefaults = {
    zurg: 'manual', sonarr: 'automatic', radarr: 'automatic', lidarr: 'automatic',
    'lidarr-postgres': 'manual', prowlarr: 'automatic', seerrng: 'automatic',
    jellyfin: 'manual', bazarr: 'automatic', whisparr: 'manual', neutarr: 'manual',
    maintainerr: 'manual', profilarr: 'manual', 'profilarr-parser': 'manual', homarr: 'automatic',
  };

  const policyCards = [...document.querySelectorAll('[data-policy-service]')];
  const initiallyHidden = new Set(policyCards.filter(el => el.hidden).map(el => el.dataset.policyService));
  const initialized = new Set(policyCards.filter(el => !el.hidden).map(el => el.dataset.policyService));

  function expandedSelection() {
    const selected = new Set(
      [...form.querySelectorAll('input[name="services"]:checked')].map(el => el.value)
    );
    let changed = true;
    while (changed) {
      changed = false;
      for (const key of [...selected]) {
        for (const dep of dependencyMap[key] || []) {
          if (!selected.has(dep)) {
            selected.add(dep);
            changed = true;
          }
        }
      }
    }
    return selected;
  }

  function globalMode() {
    return form.querySelector('input[name="update_mode"]:checked')?.value || 'review';
  }

  function desiredDefault(key) {
    if (blockedDefaults.has(key)) return 'manual';
    const mode = globalMode();
    if (mode === 'automatic') return catalogDefaults[key] || 'automatic';
    return mode;
  }

  function syncPolicies() {
    const selected = expandedSelection();
    for (const card of policyCards) {
      const key = card.dataset.policyService;
      const shouldShow = selected.has(key);
      const wasHidden = card.hidden;
      card.hidden = !shouldShow;
      if (!shouldShow) continue;

      const select = card.querySelector('select');
      if (!select) continue;

      // A service selected during this wizard did not have a server-rendered
      // policy yet, so browsers otherwise fall back to the first option
      // (Manual). Give newly-selected services the current global policy while
      // keeping high-risk dependencies such as Zurg/PostgreSQL/Jellyfin manual.
      if ((wasHidden || initiallyHidden.has(key)) && !initialized.has(key)) {
        select.value = desiredDefault(key);
        initialized.add(key);
      }
    }
  }

  // The original inline wizard understands visible checkbox services. This
  // follow-up layer also expands hidden dependencies (for example Lidarr ->
  // PostgreSQL) and fixes policy defaults for services selected mid-wizard.
  form.querySelectorAll('input[name="services"], input[name="update_mode"]').forEach(el => {
    el.addEventListener('change', () => requestAnimationFrame(syncPolicies));
  });
  document.querySelectorAll('[data-next],[data-prev],.setup-step-tab').forEach(el => {
    el.addEventListener('click', () => requestAnimationFrame(syncPolicies));
  });

  requestAnimationFrame(syncPolicies);
})();
