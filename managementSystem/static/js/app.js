// Frontend-only interactivity - no backend
document.addEventListener('DOMContentLoaded', () => {
  // ── Login: role toggle + form validation + demo redirect ──
  const roleBtns = document.querySelectorAll('[data-role="admin"], [data-role="accountant"]');
  roleBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      roleBtns.forEach(b => {
        b.className = 'flex-1 py-2 font-label-caps text-label-caps uppercase text-on-surface-variant hover:bg-surface-container-high transition-colors rounded-DEFAULT';
      });
      btn.className = 'flex-1 py-2 font-label-caps text-label-caps uppercase text-primary bg-surface-container-high border border-outline-variant rounded-DEFAULT transition-colors';
      const role = btn.dataset.role;
      const subtitle = document.querySelector('[data-login-subtitle]');
      if (subtitle) subtitle.textContent = role === 'accountant' ? 'Accountant access - billing & reports.' : 'Access your administrative dashboard.';
    });
  });

  const loginForm = document.querySelector('[data-login-form]');
  if (loginForm) {
    loginForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const email = loginForm.querySelector('#email')?.value.trim();
      const pwd = loginForm.querySelector('#password')?.value.trim();
      const msg = loginForm.querySelector('[data-form-msg]');
      if (!email || !pwd) {
        if (msg) { msg.textContent = 'Please fill email and password.'; msg.classList.remove('hidden'); }
        toast('Please fill all fields', 'error');
        return;
      }
      if (!email.includes('@')) {
        if (msg) { msg.textContent = 'Invalid email format.'; msg.classList.remove('hidden'); }
        return;
      }
      toast('Welcome back! Redirecting...', 'success');
      setTimeout(() => window.location.href = '/dashboard/', 700);
    });
  }

  document.querySelectorAll('[data-toast]').forEach(btn => {
    btn.addEventListener('click', () => toast(btn.dataset.toast || 'Coming soon (frontend demo)', 'info'));
  });

  // ── Generic table search ──
  document.querySelectorAll('[data-table-search]').forEach(input => {
    const target = input.dataset.tableSearch;
    const table = document.querySelector(target);
    if (!table) return;
    input.addEventListener('input', () => {
      const q = input.value.toLowerCase();
      table.querySelectorAll('tbody tr').forEach(tr => {
        tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
    });
  });

  // Global search in header (filters dashboard_content tables)
  const globalSearch = document.querySelector('[data-global-search]');
  if (globalSearch) {
    globalSearch.addEventListener('input', () => {
      const q = globalSearch.value.toLowerCase();
      document.querySelectorAll('main tbody tr').forEach(tr => {
        if (!q) tr.style.display = '';
        else tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
      });
      document.querySelectorAll('[data-filter-card]').forEach(card => {
        const txt = card.textContent.toLowerCase();
        card.style.display = !q || txt.includes(q) ? '' : 'none';
      });
    });
  }

  // ── Members drawer ──
  const drawer = document.querySelector('[data-drawer]');
  const overlay = document.querySelector('[data-drawer-overlay]');
  function openDrawer(name) {
    if (!drawer) return;
    drawer.classList.remove('hidden', 'lg:hidden');
    drawer.classList.add('flex');
    if (overlay) overlay.classList.remove('hidden');
    const title = drawer.querySelector('[data-drawer-name]');
    if (title && name) title.textContent = name;
    const viewBtn = document.getElementById('drawer-view-details');
    if(viewBtn && name) viewBtn.href = '/members/detail/?member=' + encodeURIComponent(name);
    document.body.style.overflow = 'hidden';
  }
  function closeDrawer() {
    if (!drawer) return;
    drawer.classList.add('hidden');
    drawer.classList.remove('flex');
    if (overlay) overlay.classList.add('hidden');
    document.body.style.overflow = '';
  }
  document.querySelectorAll('[data-open-drawer]').forEach(tr => {
    tr.addEventListener('click', () => openDrawer(tr.dataset.openDrawer || tr.querySelector('td')?.textContent.trim()));
  });
  document.querySelectorAll('[data-close-drawer]').forEach(b => b.addEventListener('click', closeDrawer));
  if (overlay) overlay.addEventListener('click', closeDrawer);

  // ── Status filter (members) ──
  const statusFilter = document.querySelector('[data-status-filter]');
  if (statusFilter) {
    statusFilter.addEventListener('change', () => {
      const v = statusFilter.value.toLowerCase();
      const tbody = document.querySelector('[data-members-tbody]');
      if (!tbody) return;
      tbody.querySelectorAll('tr').forEach(tr => {
        const statusCell = tr.querySelector('td:nth-child(4)')?.textContent.toLowerCase() || '';
        tr.style.display = (v === 'all statuses' || v === '' || statusCell.includes(v)) ? '' : 'none';
      });
    });
  }

  // ── Add member demo ──
  const addBtn = document.querySelector('[data-add-member]');
  if (addBtn) addBtn.addEventListener('click', () => toast('Add Member modal (frontend demo) - backend later', 'info'));

  // ── Sidebar collapse/expand ──
  const sidebar = document.getElementById('sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  if (sidebar && sidebarToggle) {
    const saved = localStorage.getItem('sidebar-collapsed');
    if (saved === '1') sidebar.classList.add('sidebar-collapsed');
    sidebarToggle.addEventListener('click', () => {
      sidebar.classList.toggle('sidebar-collapsed');
      const collapsed = sidebar.classList.contains('sidebar-collapsed');
      localStorage.setItem('sidebar-collapsed', collapsed ? '1' : '0');
      sidebarToggle.querySelector('span').textContent = collapsed ? 'chevron_right' : 'chevron_left';
      sidebarToggle.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    });
    // set initial icon
    if (sidebar.classList.contains('sidebar-collapsed')) {
      sidebarToggle.querySelector('span').textContent = 'chevron_right';
    }
  }

  // ── Sidebar groups (collapsible) ──
  document.querySelectorAll('[data-sidebar-group-toggle]').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.sidebarGroupToggle;
      const group = document.querySelector(`[data-sidebar-group="${key}"]`);
      if (!group) return;
      group.classList.toggle('hidden');
      const icon = btn.querySelector('[data-icon="expand"]');
      if (icon) icon.textContent = group.classList.contains('hidden') ? 'expand_more' : 'expand_less';
    });
  });

  // ── Admin avatar from settings (frontend-only localStorage) ──
  const savedAvatar = localStorage.getItem('admin-avatar');
  const savedName = localStorage.getItem('admin-name');
  const savedRole = localStorage.getItem('admin-role');
  const headerAvatar = document.getElementById('admin-avatar');
  const headerRole = document.getElementById('admin-role-display');
  if(savedAvatar && headerAvatar) headerAvatar.src = savedAvatar;
  if(savedRole && headerRole) headerRole.textContent = savedRole;
  // Also handle name if displayed elsewhere (optional)


  // ── Confirmation modals (frontend-only) ──
  const modal = document.getElementById('confirm-modal');
  const modalTitle = document.getElementById('confirm-title');
  const modalMsg = document.getElementById('confirm-message');
  const modalConfirm = document.getElementById('confirm-action');
  let pendingAction = null;
  function openConfirm(title, msg, confirmText='Confirm', type='error') {
    if (!modal) return;
    if (modalTitle) modalTitle.textContent = title;
    if (modalMsg) modalMsg.textContent = msg;
    if (modalConfirm) {
      modalConfirm.textContent = confirmText;
      modalConfirm.className = type==='error' ? 'px-6 py-2 bg-error text-on-error font-label-caps text-[11px] hover:bg-error-container' : 'px-6 py-2 bg-primary text-surface-dim font-label-caps text-[11px]';
    }
    modal.classList.remove('hidden');
    document.body.style.overflow='hidden';
  }
  function closeConfirm(){ if(modal){ modal.classList.add('hidden'); document.body.style.overflow=''; pendingAction=null; } }
  document.querySelectorAll('[data-close-modal]').forEach(b=>b.addEventListener('click', closeConfirm));
  if(modal) modal.addEventListener('click', e=>{ if(e.target===modal || e.target.hasAttribute('data-close-modal')) closeConfirm(); });
  if(modalConfirm) modalConfirm.addEventListener('click', ()=>{ if(pendingAction) pendingAction(); closeConfirm(); toast('Confirmed (frontend demo)','success'); });
  document.querySelectorAll('[data-confirm]').forEach(btn=>{
    btn.addEventListener('click', e=>{
      e.preventDefault();
      const title = btn.dataset.confirmTitle || 'Are you sure?';
      const msg = btn.dataset.confirm || btn.getAttribute('data-confirm') || 'This action cannot be undone.';
      const confirmText = btn.dataset.confirmText || 'Confirm';
      pendingAction = ()=>{};
      openConfirm(title, msg, confirmText, 'error');
    });
  });

  // ── Member Actions dropdown (View/Edit/Delete/Suspend/Activate/Create Subscription) ──
  document.querySelectorAll('[data-action-toggle]').forEach(btn=>{
    btn.addEventListener('click', e=>{
      e.stopPropagation();
      const menu = btn.nextElementSibling;
      if(!menu || !menu.hasAttribute('data-action-menu')) return;
      document.querySelectorAll('[data-action-menu]').forEach(m=>{ if(m!==menu) m.classList.add('hidden'); });
      menu.classList.toggle('hidden');
    });
  });
  document.addEventListener('click', ()=>{ document.querySelectorAll('[data-action-menu]').forEach(m=>m.classList.add('hidden')); });
  document.querySelectorAll('[data-action="suspend"], [data-action="activate"]').forEach(btn=>{
    btn.addEventListener('click', e=>{
      e.stopPropagation();
      const member = btn.dataset.member || 'this member';
      const isSuspend = btn.dataset.action === 'suspend';
      const title = isSuspend ? 'Suspend Member' : 'Activate Member';
      const msg = isSuspend ? `Are you sure you want to suspend ${member}? They will be blocked from check-in.` : `Are you sure you want to activate ${member}?`;
      const row = btn.closest('tr');
      pendingAction = ()=>{
        if(!row) return;
        const chip = row.querySelector('td:nth-child(4) div');
        const suspendBtn = row.querySelector('[data-action="suspend"]');
        const activateBtn = row.querySelector('[data-action="activate"]');
        if(isSuspend){
          if(chip){ chip.innerHTML='<div class="w-[6px] h-[6px] bg-error"></div> SUSPENDED'; chip.className='inline-flex items-center gap-2 bg-surface-container-highest border border-outline-variant px-2 py-1 rounded font-label-caps text-label-caps text-on-surface'; }
          if(suspendBtn) suspendBtn.classList.add('hidden'); if(activateBtn) activateBtn.classList.remove('hidden');
          toast(`${member} suspended (frontend demo)`, 'success');
        } else {
          if(chip){ chip.innerHTML='<div class="w-[6px] h-[6px] bg-tertiary"></div> ACTIVE'; chip.className='inline-flex items-center gap-2 bg-surface-container-highest border border-outline-variant px-2 py-1 rounded font-label-caps text-label-caps text-on-surface'; }
          if(activateBtn) activateBtn.classList.add('hidden'); if(suspendBtn) suspendBtn.classList.remove('hidden');
          toast(`${member} activated (frontend demo)`, 'success');
        }
        btn.closest('[data-action-menu]')?.classList.add('hidden');
      };
      openConfirm(title, msg, isSuspend ? 'Suspend' : 'Activate', isSuspend ? 'error' : 'success');
    });
  });

  // ── Pagination demo ──
  document.querySelectorAll('[data-page]').forEach(b => b.addEventListener('click', () => toast('Pagination (frontend demo)', 'info')));

  // ── Generic fallback: every remaining static button becomes functional via toast (frontend-only) ──
  document.querySelectorAll('main button, main a').forEach(el => {
    if (el.hasAttribute('data-toast') || el.hasAttribute('data-role') || el.hasAttribute('data-close-drawer') || el.hasAttribute('data-add-member') || el.hasAttribute('data-page') || el.hasAttribute('data-action-toggle') || el.hasAttribute('data-action') || el.hasAttribute('data-confirm') || el.hasAttribute('data-sidebar-group-toggle') || el.closest('[data-open-drawer]') || el.closest('[data-action-menu]')) return;
    if (el.tagName === 'A' && el.getAttribute('href') && el.getAttribute('href').startsWith('/')) return;
    if (el.closest('nav') || el.closest('header')) return;
    el.addEventListener('click', (e) => {
      if (el.type === 'submit') return;
      e.preventDefault();
      const txt = (el.textContent || '').trim();
      if (!txt) return;
      // Filter/Sort get specific messages
      const lower = txt.toLowerCase();
      let msg = txt + ' (frontend demo)';
      if(lower.includes('filter')) msg='Filter applied (frontend demo)';
      else if(lower.includes('sort')) msg='Sorted (frontend demo)';
      else if(lower.includes('manual entry')) msg='Manual entry opened (frontend demo)';
      else if(lower.includes('view full log')) msg='Full log opened (frontend demo)';
      else if(lower.includes('override')) msg='Override granted (frontend demo)';
      toast(msg, 'info');
    });
  });
});

function toast(msg, type='info') {
  let c = document.getElementById('toast-container');
  if (!c) {
    c = document.createElement('div');
    c.id = 'toast-container';
    c.className = 'fixed bottom-4 right-4 z-50 flex flex-col gap-2';
    document.body.appendChild(c);
  }
  const el = document.createElement('div');
  const bg = type==='error' ? 'bg-error text-on-error-container border-error' : type==='success' ? 'bg-tertiary-container text-on-tertiary-container border-tertiary' : 'bg-surface-container-high border-outline-variant text-on-surface';
  el.className = `px-4 py-3 rounded border text-sm font-label-caps shadow-lg ${bg} transition-all`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => { el.style.opacity='0'; setTimeout(()=>el.remove(),300); }, 2500);
}
