(() => {
  function init() {
    const board = document.querySelector('.arr-board-v2');
    if (!board) return;
    const titlebar = board.querySelector('.board-v2-titlebar');
    const stats = board.querySelector('.board-title-stats');
    const edit = board.querySelector('#board-edit-toggle');
    const add = board.querySelector('#board-add-widget');
    if (!titlebar || !edit) return;

    let actions = board.querySelector('.board-layout-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'board-layout-actions';
      if (stats) titlebar.insertBefore(actions, stats);
      else titlebar.appendChild(actions);
    }
    if (edit.parentElement !== actions) actions.appendChild(edit);
    if (add && add.parentElement !== actions) actions.appendChild(add);

    let help = board.querySelector('.board-editor-help');
    if (!help) {
      help = document.createElement('div');
      help.className = 'board-editor-help';
      help.innerHTML = '<strong>Layout editor</strong><span class="edit-tip"><i class="edit-dot"></i>Drag MOVE to reposition</span><span class="edit-tip">↘ Drag the coloured corner to resize</span><span class="edit-tip">Changes save when you press Save & exit</span>';
      const grid = board.querySelector('#board-grid');
      if (grid && grid.parentNode) grid.parentNode.insertBefore(help, grid);
    }

    const sync = () => {
      const editing = board.classList.contains('editing');
      edit.textContent = editing ? 'Save & exit' : 'Edit layout';
      edit.setAttribute('aria-pressed', editing ? 'true' : 'false');
      if (add) add.textContent = '+ Add card';
    };
    sync();
    new MutationObserver(sync).observe(board, {attributes:true, attributeFilter:['class']});

    // Prevent the browser-native HTML drag operation from stealing resize gestures.
    board.addEventListener('pointerdown', (event) => {
      if (!board.classList.contains('editing')) return;
      const handle = event.target.closest('.widget-resize-handle');
      if (!handle) return;
      const widget = handle.closest('.board-v2-widget');
      if (widget) widget.draggable = false;
    }, true);
    board.addEventListener('pointerup', () => {
      if (!board.classList.contains('editing')) return;
      board.querySelectorAll('.board-v2-widget').forEach((widget) => widget.draggable = true);
    }, true);

    // Make the MOVE grip the obvious place to reorder cards while still supporting
    // the existing dashboard_boards.js drag/drop engine.
    board.addEventListener('mousedown', (event) => {
      if (!board.classList.contains('editing')) return;
      const grip = event.target.closest('.widget-drag-handle');
      if (!grip) return;
      const widget = grip.closest('.board-v2-widget');
      if (widget) widget.draggable = true;
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
