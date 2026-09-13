(() => {
  'use strict';

  if (!window.ArrNexusMagicIntake) return;

  const state = {
    selected: new Set(),
    toolbar: null,
    count: null,
  };

  const esc = value => String(value ?? '')
    .replace(/[&<>'"]/g, ch => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[ch]));

  function cardKey(card) {
    return card?.dataset?.groupKey || '';
  }

  function cards() {
    return Array.from(
      document.querySelectorAll(
        '#magic-grid .magic-card'
      )
    );
  }

  function selectedKeys() {
    return Array.from(state.selected);
  }

  function updateToolbar() {
    if (state.count) {
      state.count.textContent =
        `${state.selected.size} selected`;
    }

    document
      .querySelectorAll('[data-smart-bulk-action]')
      .forEach(button => {
        button.disabled = state.selected.size === 0;
      });
  }

  function selectCard(card, selected) {
    const key = cardKey(card);
    if (!key) return;

    const checkbox = card.querySelector(
      '[data-smart-select]'
    );

    if (selected) {
      state.selected.add(key);
      card.classList.add('magic-card-selected');
      if (checkbox) checkbox.checked = true;
    } else {
      state.selected.delete(key);
      card.classList.remove('magic-card-selected');
      if (checkbox) checkbox.checked = false;
    }

    updateToolbar();
  }

  function enhanceCard(card) {
    if (!card || card.dataset.smartControls === '1') {
      return;
    }

    card.dataset.smartControls = '1';

    const key = cardKey(card);
    if (!key) return;

    const selector = document.createElement('label');
    selector.className = 'magic-smart-select';
    selector.title = 'Select this intake item';

    selector.innerHTML =
      '<input type="checkbox" data-smart-select aria-label="Select intake item">' +
      '<span></span>';

    card.appendChild(selector);

    const checkbox = selector.querySelector(
      '[data-smart-select]'
    );

    checkbox.addEventListener('change', () => {
      selectCard(card, checkbox.checked);
    });

    if (state.selected.has(key)) {
      selectCard(card, true);
    }

    // "Ignore" is now the safer and clearer "Clear".
    const oldForm = card.querySelector(
      'form[action="/magic-intake/ignore"]'
    );

    const oldButton = oldForm?.querySelector('button');

    if (oldButton) {
      oldButton.textContent = 'Clear';
      oldButton.title =
        'Remove permanently from Magic Intake. Media and Real-Debrid content are left untouched.';
    }
  }

  function enhanceAll() {
    cards().forEach(enhanceCard);
  }

  function installStyles() {
    if (document.querySelector('#magic-v14-style')) {
      return;
    }

    const style = document.createElement('style');
    style.id = 'magic-v14-style';

    style.textContent = `
      .magic-card {
        position: relative;
      }

      .magic-smart-select {
        position: absolute;
        z-index: 8;
        top: 8px;
        left: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        border-radius: 8px;
        background: rgba(8, 18, 34, .88);
        border: 1px solid rgba(255,255,255,.24);
        cursor: pointer;
        backdrop-filter: blur(4px);
      }

      .magic-smart-select input {
        width: 17px;
        height: 17px;
        margin: 0;
        cursor: pointer;
      }

      .magic-card-selected {
        outline: 2px solid var(--accent, #20c997);
        outline-offset: -2px;
      }

      .magic-smart-toolbar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px;
        padding: 10px 0 14px;
      }

      .magic-smart-toolbar .magic-smart-count {
        font-weight: 700;
        margin-right: 4px;
      }

      .magic-smart-toolbar .magic-smart-note {
        margin-left: auto;
        font-size: .82rem;
      }

      .magic-smart-toolbar button.danger-soft {
        border-color: rgba(255,100,100,.45);
      }

      @media (max-width: 850px) {
        .magic-smart-toolbar .magic-smart-note {
          width: 100%;
          margin-left: 0;
        }
      }
    `;

    document.head.appendChild(style);
  }

  function installToolbar() {
    if (
      document.querySelector(
        '#magic-smart-toolbar'
      )
    ) {
      state.toolbar =
        document.querySelector(
          '#magic-smart-toolbar'
        );

      state.count =
        state.toolbar.querySelector(
          '.magic-smart-count'
        );

      return;
    }

    const grid = document.querySelector(
      '#magic-grid'
    );

    if (!grid) return;

    const toolbar = document.createElement('div');
    toolbar.id = 'magic-smart-toolbar';
    toolbar.className = 'magic-smart-toolbar';

    toolbar.innerHTML = `
      <span class="magic-smart-count">0 selected</span>

      <button type="button"
              class="secondary"
              data-smart-select-visible>
        Select visible
      </button>

      <button type="button"
              class="secondary"
              data-smart-clear-selection>
        Clear selection
      </button>

      <button type="button"
              class="secondary"
              data-smart-bulk-action="recheck"
              disabled>
        Recheck selected
      </button>

      <button type="button"
              class="secondary danger-soft"
              data-smart-bulk-action="clear"
              disabled>
        Clear from Intake
      </button>

      <span class="muted magic-smart-note">
        Match % = identity confidence, not library completion.
        Clear from Intake does not delete media or Real-Debrid content.
      </span>
    `;

    grid.parentNode.insertBefore(
      toolbar,
      grid
    );

    state.toolbar = toolbar;
    state.count = toolbar.querySelector(
      '.magic-smart-count'
    );

    toolbar
      .querySelector(
        '[data-smart-select-visible]'
      )
      .addEventListener('click', () => {
        const visible = cards().filter(card => {
          return card.offsetParent !== null;
        });

        const allSelected =
          visible.length > 0 &&
          visible.every(card =>
            state.selected.has(cardKey(card))
          );

        visible.forEach(card =>
          selectCard(card, !allSelected)
        );
      });

    toolbar
      .querySelector(
        '[data-smart-clear-selection]'
      )
      .addEventListener('click', () => {
        cards().forEach(card =>
          selectCard(card, false)
        );

        state.selected.clear();
        updateToolbar();
      });

    toolbar
      .querySelector(
        '[data-smart-bulk-action="recheck"]'
      )
      .addEventListener('click', async button => {
        await bulkRecheck(
          button.currentTarget
        );
      });

    toolbar
      .querySelector(
        '[data-smart-bulk-action="clear"]'
      )
      .addEventListener('click', async button => {
        await bulkClear(
          button.currentTarget
        );
      });
  }

  async function jsonPost(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    let data = {};

    try {
      data = await response.json();
    } catch (_) {}

    if (!response.ok) {
      throw new Error(
        data.detail ||
        `HTTP ${response.status}`
      );
    }

    return data;
  }

  function triggerExistingReload(delay = 250) {
    setTimeout(() => {
      const stateFilter = document.querySelector(
        '#magic-filter-state'
      );

      if (stateFilter) {
        stateFilter.dispatchEvent(
          new Event(
            'change',
            { bubbles: true }
          )
        );
      } else {
        location.reload();
      }
    }, delay);
  }

  async function inspectOne(button, key) {
    if (!key) return;

    const before = button.textContent;

    button.disabled = true;
    button.textContent = 'Checking…';

    try {
      const result = await jsonPost(
        '/api/magic-intake/inspect',
        { group_key: key }
      );

      button.textContent =
        result.state === 'resolved'
          ? 'Complete'
          : 'Checked';

      triggerExistingReload(150);

    } catch (error) {
      button.disabled = false;
      button.textContent = before;
      alert(error.message);
    }
  }

  async function bulkRecheck(button) {
    const keys = selectedKeys();

    if (!keys.length) return;

    const before = button.textContent;
    button.disabled = true;
    button.textContent =
      `Queuing ${keys.length}…`;

    try {
      const result = await jsonPost(
        '/api/magic-intake/bulk-recheck',
        { group_keys: keys }
      );

      button.textContent =
        result.queued
          ? 'Rechecking…'
          : 'Already running';

      // The backend deliberately processes them sequentially.
      triggerExistingReload(1500);
      triggerExistingReload(4000);
      triggerExistingReload(8000);

      setTimeout(() => {
        if (button.isConnected) {
          button.textContent = before;
          button.disabled =
            state.selected.size === 0;
        }
      }, 9000);

    } catch (error) {
      button.textContent = before;
      button.disabled = false;
      alert(error.message);
    }
  }

  async function clearKeys(keys) {
    return jsonPost(
      '/api/magic-intake/bulk-clear',
      { group_keys: keys }
    );
  }

  async function bulkClear(button) {
    const keys = selectedKeys();

    if (!keys.length) return;

    const ok = confirm(
      `Clear ${keys.length} selected item(s) from Magic Intake?\n\n` +
      `This does NOT delete the media, the Real-Debrid source, or the title from Radarr/Sonarr.`
    );

    if (!ok) return;

    const before = button.textContent;

    button.disabled = true;
    button.textContent = 'Clearing…';

    try {
      await clearKeys(keys);

      cards().forEach(card => {
        const key = cardKey(card);

        if (keys.includes(key)) {
          card.remove();
          state.selected.delete(key);
        }
      });

      updateToolbar();
      triggerExistingReload(100);

    } catch (error) {
      button.textContent = before;
      button.disabled = false;
      alert(error.message);
    }
  }

  // ------------------------------------------------------------------
  // Recheck buttons:
  // replace the old verification-only action with the v13.4 targeted
  // Arr/source-name inspection.
  // ------------------------------------------------------------------

  document.addEventListener(
    'click',
    event => {
      const button = event.target.closest(
        '[data-magic-recheck]'
      );

      if (!button) return;

      event.preventDefault();
      event.stopImmediatePropagation();

      const card = button.closest(
        '.magic-card'
      );

      inspectOne(
        button,
        cardKey(card)
      );
    },
    true
  );

  // ------------------------------------------------------------------
  // Per-card Clear:
  // intercept the old Ignore form and use the permanent source ledger.
  // ------------------------------------------------------------------

  document.addEventListener(
    'submit',
    async event => {
      const form = event.target.closest(
        'form[action="/magic-intake/ignore"]'
      );

      if (!form) return;

      event.preventDefault();
      event.stopImmediatePropagation();

      const card = form.closest(
        '.magic-card'
      );

      const key = cardKey(card);

      if (!key) return;

      const ok = confirm(
        'Clear this item permanently from Magic Intake?\n\n' +
        'The media and its Real-Debrid/source content will NOT be deleted.'
      );

      if (!ok) return;

      const button = form.querySelector(
        'button'
      );

      if (button) {
        button.disabled = true;
        button.textContent = 'Clearing…';
      }

      try {
        await clearKeys([key]);

        state.selected.delete(key);
        card?.remove();

        updateToolbar();
        triggerExistingReload(100);

      } catch (error) {
        if (button) {
          button.disabled = false;
          button.textContent = 'Clear';
        }

        alert(error.message);
      }
    },
    true
  );

  // Dynamic card loader in the original app.js replaces card HTML.
  // MutationObserver adds the v13.4 controls back automatically.
  const observer = new MutationObserver(() => {
    enhanceAll();
  });

  function init() {
    installStyles();
    installToolbar();
    enhanceAll();
    updateToolbar();

    const grid = document.querySelector(
      '#magic-grid'
    );

    if (grid) {
      observer.observe(
        grid,
        {
          childList: true,
          subtree: true,
        }
      );
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener(
      'DOMContentLoaded',
      init,
      { once: true }
    );
  } else {
    init();
  }
})();
