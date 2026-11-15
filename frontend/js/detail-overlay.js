/* One mounted detail controller, shared by Browse and direct detail entries. */
(() => {
  const browseMounted = !!document.getElementById('grid') || !!document.getElementById('askMain');
  const mountId = crypto.randomUUID();
  let dialog, templatePromise, revision = 0, source = null, closeTimer;
  let originCard = null, scrollY = 0, savedBodyStyle = '', background = [];
  const isDetail = () => location.pathname.endsWith('/detail.html');
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  history.scrollRestoration = 'manual';

  async function ensureDialog() {
    if (dialog) return dialog;
    if (!templatePromise) templatePromise = (async () => {
      let main = document.querySelector('.detail-workspace');
      if (!main) {
        // Fetch only the shared detail template, never a replacement Browse background.
        const response = await fetch('detail.html');
        if (!response.ok) throw new Error('Unable to open research details. Please try again.');
        const documentTemplate = new DOMParser().parseFromString(await response.text(), 'text/html');
        main = documentTemplate.querySelector('.detail-workspace');
        if (!main) throw new Error('Research detail template is unavailable.');
        main = document.importNode(main, true);
      }
      dialog = document.createElement('dialog');
      dialog.className = 'detail-dialog';
      dialog.setAttribute('aria-labelledby', 'paperTitle');
      dialog.setAttribute('aria-modal', 'true');
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn btn-ghost btn-sm overlay-close';
      close.textContent = 'Close';
      close.setAttribute('aria-label', 'Close research detail');
      close.addEventListener('click', requestClose);
      dialog.append(close, main);
      document.body.appendChild(dialog);
      dialog.addEventListener('cancel', event => { event.preventDefault(); requestClose(); });
      dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('a[href], button, input, textarea, select, summary, [tabindex="0"]')]
          .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first?.focus();
        }
      });
      return dialog;
    })().catch(error => { templatePromise = null; throw error; });
    return templatePromise;
  }

  function lockBackground() {
    scrollY = window.scrollY;
    savedBodyStyle = document.body.getAttribute('style') || '';
    const scrollbar = innerWidth - document.documentElement.clientWidth;
    Object.assign(document.body.style, {position: 'fixed', top: `-${scrollY}px`, width: '100%', overflow: 'hidden', paddingRight: `${scrollbar}px`});
    background = [...document.body.children].filter(el => el !== dialog).map(el => [el, el.inert]);
    background.forEach(([el]) => { el.inert = true; });
  }
  function unlockBackground() {
    background.forEach(([el, inert]) => { el.inert = inert; });
    document.body.setAttribute('style', savedBodyStyle);
    document.body.style.visibility = 'visible';
    window.scrollTo(0, scrollY);
    if (originCard?.isConnected) originCard.focus({preventScroll: true});
  }
  function requestClose() {
    if (browseMounted && history.state?.detailMount === mountId) history.go(-history.state.detailDepth);
    else location.assign('ask.html');
  }
  function hide() {
    ++revision;
    source = null;
    window.iPeekDetail.close();
    if (!dialog?.open) return;
    dialog.classList.remove('is-open');
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => { dialog.close(); unlockBackground(); }, reduced() ? 0 : 230);
  }
  async function show() {
    const nextSource = new URLSearchParams(location.search).get('id');
    if (dialog?.open && source === nextSource) return;
    const ticket = ++revision;
    window.iPeekDetail.close();
    source = nextSource;
    clearTimeout(closeTimer);
    try {
      await ensureDialog();
      if (ticket !== revision || !isDetail()) return;
      if (!dialog.open) {
        lockBackground();
        dialog.showModal();
      }
      dialog.querySelector('.overlay-close').focus({preventScroll: true});
      // The controller sets loading states synchronously before awaiting APIs.
      window.iPeekDetail.open();
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (ticket === revision) dialog.classList.add('is-open');
      }));
    } catch (error) {
      if (ticket !== revision) return;
      source = null;
      toast(error.message, 'error');
      if (browseMounted) requestClose();
    }
  }
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target || link.hasAttribute('download')) return;
    if (link.hasAttribute('data-detail-close')) {
      event.preventDefault(); requestClose(); return;
    }
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !url.pathname.endsWith('/detail.html')) return;
    event.preventDefault();
    if (!dialog?.open) originCard = link;
    if (url.href === location.href && dialog?.open) return;
    const depth = history.state?.detailMount === mountId ? history.state.detailDepth + 1 : 1;
    history.pushState({detailMount: mountId, detailDepth: depth}, '', url);
    show();
  });
  addEventListener('popstate', () => isDetail() ? show() : hide());
  function viewport() {
    const view = window.visualViewport;
    document.documentElement.style.setProperty('--reader-viewport', `${view?.height || innerHeight}px`);
    document.documentElement.style.setProperty('--reader-offset', `${view?.offsetTop || 0}px`);
  }
  window.visualViewport?.addEventListener('resize', viewport);
  window.visualViewport?.addEventListener('scroll', viewport);
  addEventListener('resize', viewport);
  viewport();
  if (!browseMounted) document.body.classList.add('detail-direct');
  if (isDetail()) show();
})();
