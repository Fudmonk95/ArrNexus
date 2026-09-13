(() => {
  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function injectBoardSwitcher() {
    if (window.location.pathname !== '/' || q('.arr-board-v2')) return;
    const current = new URL(window.location.href).searchParams.get('board') || 'overview';
    fetch('/api/dashboard/boards', {credentials: 'same-origin'})
      .then((r) => r.ok ? r.json() : Promise.reject(new Error('board API unavailable')))
      .then((data) => {
        const boards = Array.isArray(data.boards) ? data.boards : [];
        if (!boards.length || q('.dashboard-board-injected')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'dashboard-board-injected';
        const tabs = document.createElement('div');
        tabs.className = 'board-switcher';
        boards.forEach((board) => {
          const a = document.createElement('a');
          a.className = 'board-v2-tab' + (board.id === current ? ' active' : '');
          a.textContent = board.name;
          a.href = board.id === 'overview' ? '/' : '/?board=' + encodeURIComponent(board.id);
          tabs.appendChild(a);
        });
        const manage = document.createElement('a');
        manage.className = 'button';
        manage.href = '/dashboard/boards';
        manage.textContent = 'Board settings';
        wrapper.append(tabs, manage);
        const target = q('.page-head');
        if (target && target.parentNode) target.parentNode.insertBefore(wrapper, target);
      })
      .catch(() => {});
  }

  function cssColor(name, fallback) {
    const board = q('.arr-board-v2');
    return board ? (getComputedStyle(board).getPropertyValue(name).trim() || fallback) : fallback;
  }

  function drawChart(canvas) {
    if (!canvas || !canvas.getContext) return;
    let values = [];
    try { values = JSON.parse(canvas.dataset.values || '[]').map(Number).filter(Number.isFinite); } catch (_) {}
    if (!values.length) values = [0, 0];
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 2));
    const width = Math.max(40, Math.round(rect.width));
    const height = Math.max(32, Math.round(rect.height));
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const maxValue = Math.max(100, ...values);
    const step = width / Math.max(1, values.length - 1);
    const points = values.map((value, i) => [i * step, height - 5 - ((Math.max(0, value) / maxValue) * (height - 10))]);
    const accent = canvas.dataset.series === 'memory' ? '#38bdf8' : cssColor('--board-accent', '#8b5cf6');
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, accent + '55');
    gradient.addColorStop(1, accent + '00');
    ctx.beginPath();
    ctx.moveTo(points[0][0], height);
    points.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(points[points.length - 1][0], height);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.stroke();
  }

  function drawAllCharts() { qa('.metric-chart').forEach(drawChart); }

  function startClock() {
    const timeEl = q('[data-board-clock]');
    const dateEl = q('[data-board-date]');
    if (!timeEl || !dateEl) return;
    const update = () => {
      const now = new Date();
      timeEl.textContent = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
      dateEl.textContent = now.toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'});
    };
    update();
    setInterval(update, 1000);
  }

  function initBoardV2() {
    const board = q('.arr-board-v2');
    if (!board) return;
    const grid = q('#board-grid', board);
    const boardId = board.dataset.boardId;
    const columns = Number(board.dataset.columns || 12);
    const rowHeight = Number(board.dataset.rowHeight || 74);
    let editing = false;
    let dirty = false;
    let dragging = null;

    const editBtn = q('#board-edit-toggle', board);
    const addBtn = q('#board-add-widget', board);
    const dialog = q('#widget-catalogue', board);
    const search = q('#widget-search', board);

    function items() { return qa('.board-v2-widget', grid); }

    async function saveLayout() {
      const payload = {
        items: items().map((el, order) => ({
          id: el.dataset.widgetId,
          kind: el.dataset.kind,
          w: Number(el.dataset.w || 4),
          h: Number(el.dataset.h || 2),
          order,
        })),
      };
      const response = await fetch(`/api/dashboard/boards/${encodeURIComponent(boardId)}/layout`, {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await response.text());
      dirty = false;
    }

    function setEditMode(value) {
      editing = !!value;
      board.classList.toggle('editing', editing);
      editBtn.textContent = editing ? 'Save & exit' : 'Edit board';
      items().forEach((item) => item.draggable = editing);
      if (!editing && dirty) {
        editBtn.disabled = true;
        saveLayout().catch((error) => alert('Could not save board layout: ' + error.message)).finally(() => editBtn.disabled = false);
      }
    }

    editBtn?.addEventListener('click', () => setEditMode(!editing));
    addBtn?.addEventListener('click', () => dialog?.showModal());
    q('.dialog-close', dialog || document)?.addEventListener('click', () => dialog?.close());
    dialog?.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
    search?.addEventListener('input', () => {
      const term = search.value.toLowerCase().trim();
      qa('.catalogue-widget', dialog).forEach((button) => {
        const haystack = `${button.dataset.name || ''} ${button.dataset.category || ''}`.toLowerCase();
        button.hidden = !!term && !haystack.includes(term);
      });
    });

    async function addWidget(kind) {
      const options = {};
      if (kind === 'note') {
        options.title = prompt('Note title', 'Note') || 'Note';
        options.text = prompt('Note text', '') || '';
      } else if (kind === 'iframe') {
        options.title = prompt('Widget title', 'Embedded page') || 'Embedded page';
        const url = prompt('HTTPS URL to embed', 'https://');
        if (!url) return;
        options.url = url;
      } else if (kind === 'bookmarks') {
        options.title = prompt('Bookmarks title', 'Bookmarks') || 'Bookmarks';
        const raw = prompt('Enter bookmarks one per line as Name|https://url', '');
        if (raw === null) return;
        options.links = raw.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
      }
      const response = await fetch(`/api/dashboard/boards/${encodeURIComponent(boardId)}/items`, {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kind, options}),
      });
      if (!response.ok) throw new Error(await response.text());
      window.location.reload();
    }

    qa('[data-add-kind]', dialog || document).forEach((button) => button.addEventListener('click', () => {
      button.disabled = true;
      addWidget(button.dataset.addKind).catch((error) => {
        alert('Could not add widget: ' + error.message);
        button.disabled = false;
      });
    }));

    grid.addEventListener('dragstart', (event) => {
      if (!editing) return;
      const item = event.target.closest('.board-v2-widget');
      if (!item) return;
      dragging = item;
      item.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', item.dataset.widgetId || 'widget');
    });
    grid.addEventListener('dragend', () => {
      dragging?.classList.remove('dragging');
      qa('.drag-over', grid).forEach((x) => x.classList.remove('drag-over'));
      dragging = null;
    });
    grid.addEventListener('dragover', (event) => {
      if (!editing || !dragging) return;
      event.preventDefault();
      const target = event.target.closest('.board-v2-widget');
      qa('.drag-over', grid).forEach((x) => x.classList.remove('drag-over'));
      if (target && target !== dragging) target.classList.add('drag-over');
    });
    grid.addEventListener('drop', (event) => {
      if (!editing || !dragging) return;
      event.preventDefault();
      const target = event.target.closest('.board-v2-widget');
      if (!target || target === dragging) return;
      const box = target.getBoundingClientRect();
      const before = event.clientY < box.top + box.height / 2;
      grid.insertBefore(dragging, before ? target : target.nextSibling);
      dirty = true;
    });

    grid.addEventListener('click', async (event) => {
      if (!editing) return;
      const remove = event.target.closest('.widget-remove');
      if (!remove) return;
      const item = remove.closest('.board-v2-widget');
      if (!item || !confirm('Remove this widget from the board?')) return;
      const response = await fetch(`/api/dashboard/boards/${encodeURIComponent(boardId)}/items/${encodeURIComponent(item.dataset.widgetId)}/remove`, {
        method: 'POST', credentials: 'same-origin',
      });
      if (!response.ok) return alert('Could not remove widget.');
      item.remove();
      dirty = true;
    });

    grid.addEventListener('pointerdown', (event) => {
      if (!editing) return;
      const handle = event.target.closest('.widget-resize-handle');
      if (!handle) return;
      event.preventDefault();
      const item = handle.closest('.board-v2-widget');
      const startX = event.clientX, startY = event.clientY;
      const startW = Number(item.dataset.w || 4), startH = Number(item.dataset.h || 2);
      const gridBox = grid.getBoundingClientRect();
      const gap = parseFloat(getComputedStyle(grid).gap) || 14;
      const cellW = (gridBox.width - gap * (columns - 1)) / columns;
      handle.setPointerCapture?.(event.pointerId);
      const move = (e) => {
        const dx = e.clientX - startX, dy = e.clientY - startY;
        const w = Math.max(2, Math.min(columns, Math.round(startW + dx / (cellW + gap))));
        const h = Math.max(1, Math.min(8, Math.round(startH + dy / (rowHeight + gap))));
        item.dataset.w = String(w); item.dataset.h = String(h);
        item.style.gridColumn = `span ${w}`; item.style.gridRow = `span ${h}`;
        dirty = true;
        requestAnimationFrame(drawAllCharts);
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up, {once: true});
    });

    async function pollRuntime() {
      try {
        const response = await fetch(`/api/dashboard/boards/${encodeURIComponent(boardId)}/runtime`, {credentials: 'same-origin'});
        if (!response.ok) return;
        const data = await response.json();
        const m = data.metrics || {};
        const cpu = q('[data-cpu-value]', board), memory = q('[data-memory-value]', board);
        if (cpu) cpu.textContent = `${Number(m.cpu || 0).toFixed(1)}%`;
        if (memory) memory.textContent = `${Number(m.memory || 0).toFixed(1)}%`;
        const running = q('[data-live-running]', board), updates = q('[data-live-updates]', board);
        if (running) running.textContent = `${m.running || 0}/${m.total || 0}`;
        if (updates) updates.textContent = String(m.updates || 0);
        qa('.metric-chart', board).forEach((canvas) => {
          const values = canvas.dataset.series === 'memory' ? m.memory_history : m.cpu_history;
          if (Array.isArray(values)) canvas.dataset.values = JSON.stringify(values);
          drawChart(canvas);
        });
        const serviceMap = new Map((data.services || []).map((s) => [s.name, s]));
        qa('[data-service]', board).forEach((el) => {
          const service = serviceMap.get(el.dataset.service);
          if (!service) return;
          el.classList.remove('status-good', 'status-warn', 'status-bad');
          el.classList.add('status-' + (service.board_status || 'warn'));
          const text = q('[data-service-state]', el);
          if (text) text.textContent = service.status_text || service.state || 'unknown';
        });
      } catch (_) {}
    }

    setEditMode(false);
    startClock();
    drawAllCharts();
    window.addEventListener('resize', () => requestAnimationFrame(drawAllCharts));
    setInterval(() => { if (!editing) pollRuntime(); }, 10000);
  }

  injectBoardSwitcher();
  initBoardV2();
})();
