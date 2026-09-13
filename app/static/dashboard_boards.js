(() => {
  const onDashboard = window.location.pathname === '/';
  if (!onDashboard) return;

  const current = new URL(window.location.href).searchParams.get('board') || 'overview';

  fetch('/api/dashboard/boards', {credentials: 'same-origin'})
    .then((r) => r.ok ? r.json() : Promise.reject(new Error('board API unavailable')))
    .then((data) => {
      const boards = Array.isArray(data.boards) ? data.boards : [];
      if (!boards.length) return;
      const existing = document.querySelector('.board-topbar');
      if (existing) return;

      const wrapper = document.createElement('div');
      wrapper.className = 'dashboard-board-injected';
      const tabs = document.createElement('div');
      tabs.className = 'board-tabs';
      boards.forEach((board) => {
        const a = document.createElement('a');
        a.className = 'board-tab' + (board.id === current ? ' active' : '');
        a.textContent = board.name;
        a.href = board.id === 'overview' ? '/' : '/?board=' + encodeURIComponent(board.id);
        tabs.appendChild(a);
      });
      const manage = document.createElement('a');
      manage.className = 'button';
      manage.href = '/dashboard/boards';
      manage.textContent = 'Manage boards';
      wrapper.appendChild(tabs);
      wrapper.appendChild(manage);

      const target = document.querySelector('.page-head');
      if (target && target.parentNode) target.parentNode.insertBefore(wrapper, target);
    })
    .catch(() => {});
})();
