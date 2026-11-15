/* Page-only exchanges with bounded context; retries keep their original snapshot. */
(() => {
  const el = id => document.getElementById(id);
  const mobile = matchMedia('(max-width: 768px)');
  let busy = false, filtersLoading = false, filtersReady = false;
  let generation = 0, nextId = 0, selected = null, exchanges = [];
  const landingPlaceholder = el('askQuery').placeholder;
  function updateCount() { el('askCount').textContent = el('askQuery').value.length + ' / 500'; }
  function updateFilters() {
    const count = [el('askCollege').value, el('askYear').value].filter(Boolean).length;
    el('filterIndicator').hidden = !count;
    el('filterIndicator').textContent = count;
    el('filterToggle').setAttribute('aria-label', count ? 'Filter, ' + count + ' active filters' : 'Filter');
  }
  function positionFilters() {
    const panel = el('askFilters');
    if (!panel.open || mobile.matches) return;
    const anchor = el('filterToggle').getBoundingClientRect();
    panel.style.left = Math.max(12, Math.min(anchor.left, innerWidth - panel.offsetWidth - 12)) + 'px';
    panel.style.top = Math.max(12, Math.min(anchor.top - panel.offsetHeight - 12, innerHeight - panel.offsetHeight - 12)) + 'px';
  }
  function node(tag, text = '', className) {
    const value = document.createElement(tag);
    value.textContent = text;
    if (className) value.className = className;
    return value;
  }
  function announce(text) { el('askAnnouncement').textContent = text; }
  function controls() {
    el('askButton').disabled = busy;
    el('askCollege').disabled = busy || !filtersReady;
    el('askYear').disabled = busy || !filtersReady;
    el('filterRetry').disabled = busy;
    el('filterReset').disabled = busy || !filtersReady;
    exchanges.forEach(exchange => { exchange.retry.disabled = busy; });
  }
  async function loadFilters() {
    if (filtersLoading) return;
    filtersLoading = true;
    el('filterRetry').hidden = true;
    el('filterStatus').textContent = 'Loading college and year filters…';
    try {
      const data = await apiRepositoryFilters();
      el('askCollege').replaceChildren(new Option('All colleges', ''));
      data.colleges.forEach(c => el('askCollege').add(new Option(c.code + ' — ' + c.name + ' (' + c.count + ')', c.id)));
      el('askCollege').value = data.user_college?.id || '';
      el('askYear').replaceChildren(new Option('All years', ''));
      data.years.forEach(year => el('askYear').add(new Option(year, year)));
      el('filterStatus').textContent = data.years.length ? '' : 'No approved research years are available yet.';
      filtersReady = true;
    } catch (error) {
      el('filterStatus').textContent = error.message + ' You can still ask across all colleges and years.';
      el('filterRetry').hidden = false;
    } finally { filtersLoading = false; controls(); updateFilters(); positionFilters(); }
  }
  function nearBottom() {
    const box = el('messages');
    return box.scrollHeight - box.scrollTop - box.clientHeight < 90;
  }
  function followEnd() { el('messages').scrollTop = el('messages').scrollHeight; }
  // Move each source list between its mobile disclosure and the desktop sidebar.
  // One list per answer means IDs remain unique even when resizing.
  function placeSources() {
    exchanges.forEach(exchange => {
      const inSidebar = !mobile.matches && exchange === selected;
      const host = inSidebar ? el('sourceCards') : exchange.details;
      // Re-appending an already placed list would discard keyboard focus.
      if (exchange.sources.parentElement !== host) host.appendChild(exchange.sources);
      exchange.sources.hidden = !mobile.matches && !inSidebar;
      exchange.select.setAttribute('aria-pressed', String(exchange === selected));
    });
    el('selectedAnswer').textContent = selected
      ? 'Answer ' + selected.id + ': ' + selected.request.query : '';
  }
  function selectAnswer(exchange) { selected = exchange; placeSources(); }
  function sourceId(exchange, number) { return 'answer-' + exchange.id + '-source-' + number; }
  function cite(exchange, number) {
    selectAnswer(exchange);
    if (mobile.matches) exchange.details.open = true;
    const card = document.getElementById(sourceId(exchange, number));
    document.querySelectorAll('.ask-source.is-highlighted').forEach(item => item.classList.remove('is-highlighted'));
    if (card) {
      card.classList.add('is-highlighted');
      card.focus({preventScroll: true});
      card.scrollIntoView({block: 'nearest', behavior: 'instant'});
    }
  }
  function appendInline(target, text, exchange, known) {
    let offset = 0;
    for (const match of text.matchAll(/\[(\d+)\]/g)) {
      target.append(document.createTextNode(text.slice(offset, match.index)));
      const number = Number(match[1]);
      if (known.has(number)) {
        const link = node('a', match[0], 'ask-citation');
        link.href = '#' + sourceId(exchange, number);
        link.setAttribute('aria-label', 'Source ' + number + ' for answer ' + exchange.id);
        link.addEventListener('click', event => { event.preventDefault(); cite(exchange, number); });
        target.append(link);
      } else target.append(document.createTextNode(match[0]));
      offset = match.index + match[0].length;
    }
    target.append(document.createTextNode(text.slice(offset)));
  }
  function renderAnswer(exchange, data) {
    const citations = data.found ? data.citations : [];
    const known = new Set(citations.map(c => c.n));
    exchange.prose.replaceChildren();
    // Build only paragraph/list structure and known citation links; server HTML
    // remains inert text, never interpreted as markup.
    let block = null, listType = null;
    for (const line of data.answer.split(/\r?\n/)) {
      if (!line.trim()) { block = null; listType = null; continue; }
      const item = line.match(/^\s*(?:([-*•])|\d+[.)])\s+(.+)$/);
      if (item) {
        const type = item[1] ? 'ul' : 'ol';
        if (listType !== type) {
          block = node(type);
          if (type === 'ol') block.start = parseInt(line.trim(), 10);
          exchange.prose.append(block); listType = type;
        }
        const li = node('li'); appendInline(li, item[2], exchange, known); block.append(li);
      } else {
        if (!block || listType) { block = node('p'); exchange.prose.append(block); }
        else block.append(document.createTextNode('\n'));
        listType = null;
        appendInline(block, line, exchange, known);
      }
    }
    exchange.sources.replaceChildren();
    if (!citations.length) exchange.sources.append(node('p', 'No sources for this answer.', 'ask-note'));
    citations.forEach(c => {
      const card = node('article', '', 'ask-source');
      card.id = sourceId(exchange, c.n); card.tabIndex = -1;
      const heading = node('h3');
      const link = node('a', '[' + c.n + '] ' + c.title);
      link.href = 'detail.html?id=' + encodeURIComponent(c.research_id);
      heading.append(link);
      card.append(heading, node('p', c.authors || 'Authors unavailable'),
        node('p', (c.year ?? 'Year unavailable') + ' · ' + (c.college || 'College unavailable')),
        node('p', c.pages?.length ? 'Pages ' + c.pages.join(', ') : 'Pages unavailable'),
        node('p', c.snippet || 'No excerpt available.', 'source-snippet'));
      exchange.sources.append(card);
    });
  }
  function createExchange(request) {
    const id = ++nextId;
    const wrapper = node('article', '', 'ask-exchange');
    wrapper.setAttribute('aria-label', 'Question and answer ' + id);
    const question = node('p', request.query, 'ask-question');
    const answer = node('section', '', 'ask-answer');
    answer.setAttribute('aria-label', 'iPeek answer ' + id);
    const header = node('div', '', 'ask-answer-header');
    const select = node('button', 'Sources for answer ' + id, 'ask-select');
    select.type = 'button';
    header.append(node('span', 'iPeek', 'ask-label'), select);
    const prose = node('div', '', 'ask-prose');
    const retry = node('button', 'Retry this question', 'btn btn-ghost btn-sm');
    retry.type = 'button'; retry.hidden = true;
    const details = node('details', '', 'ask-mobile-sources');
    details.append(node('summary', 'Sources for answer ' + id));
    const sources = node('div', '', 'ask-source-list');
    details.append(sources);
    answer.append(header, prose, retry, details); wrapper.append(question, answer);
    const exchange = {id, request, wrapper, answer, prose, retry, select, details, sources};
    select.addEventListener('click', () => selectAnswer(exchange));
    details.addEventListener('toggle', () => { if (details.open) selectAnswer(exchange); });
    retry.addEventListener('click', () => ask(exchange));
    exchanges.push(exchange); el('messages').append(wrapper);
    return exchange;
  }
  async function ask(exchange) {
    if (busy) return;
    busy = true;
    const revision = generation;
    exchange.retry.hidden = true;
    exchange.answer.setAttribute('aria-busy', 'true');
    exchange.prose.replaceChildren(node('p', 'Searching approved research…', 'ask-loading'));
    exchange.sources.replaceChildren(node('p', 'Sources will appear with the answer.', 'ask-note'));
    selectAnswer(exchange); controls(); announce('Finding answer ' + exchange.id + '.');
    try {
      const data = await apiAskRepository(exchange.request);
      if (revision !== generation) return;
      if (typeof data.answer !== 'string' || typeof data.found !== 'boolean' || !Array.isArray(data.citations)) {
        throw new Error('The answer service returned an unexpected response. Please try again.');
      }
      const follow = nearBottom();
      exchange.result = data;
      renderAnswer(exchange, data); placeSources();
      if (follow) followEnd();
      announce('Answer ' + exchange.id + ' ready. ' + data.citations.length + ' sources.');
    } catch (error) {
      if (revision !== generation) return;
      const follow = nearBottom();
      exchange.prose.replaceChildren(node('p', error.status === 500
        ? 'The repository could not answer right now. Please try again. If this continues, report POST /search/ask to the repository maintainer.'
        : error.message, 'ask-error'));
      exchange.sources.replaceChildren(node('p', 'Sources are unavailable because this request failed.', 'ask-note'));
      exchange.retry.hidden = error.status === 401 || error.status === 422;
      if (follow) followEnd();
      announce('Answer ' + exchange.id + ' could not be completed.');
    } finally {
      // Reset invalidates the response but retains the network lock until it
      // settles, so resetting cannot cause overlapping requests.
      busy = false;
      exchange.answer.setAttribute('aria-busy', 'false'); controls();
      if (revision !== generation) announce('Ready for a new question.');
    }
  }
  el('askForm').addEventListener('submit', event => {
    event.preventDefault();
    if (busy) return;
    const query = el('askQuery').value.trim();
    if (!query || query.length > 500) {
      el('askQuery').setCustomValidity('Enter a question between 1 and 500 characters.');
      el('askQuery').reportValidity(); return;
    }
    const follow = !exchanges.length || nearBottom();
    el('askMain').classList.add('is-conversation');
    el('askQuery').placeholder = 'Ask a follow-up about this topic…';
    el('askWelcome').hidden = true; el('askSuggestions').hidden = true;
    el('conversationHeader').hidden = false; el('messages').hidden = false;
    el('sourcesPanel').hidden = false;
    let context = [];
    exchanges.forEach(previous => {
      if (!previous.result) return;
      if (previous.result.topic_changed) context = [];
      context.push({query: (previous.result.resolved_query || previous.request.query).slice(0, 1500),
        answer: previous.result.answer.slice(0, 2000),
        research_ids: previous.result.citations.map(c => c.research_id).slice(0, 12)});
    });
    const exchange = createExchange(Object.freeze({query, context: context.slice(-4),
      college_id: el('askCollege').value ? Number(el('askCollege').value) : null,
      year: el('askYear').value ? Number(el('askYear').value) : null}));
    ask(exchange);
    if (follow) followEnd();
  });
  el('askQuery').addEventListener('input', () => { el('askQuery').setCustomValidity(''); updateCount(); });
  el('askQuery').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault(); el('askForm').requestSubmit();
    }
  });
  el('newConversation').addEventListener('click', () => {
    generation++; exchanges = []; selected = null; nextId = 0;
    el('messages').replaceChildren(); el('sourceCards').replaceChildren();
    el('selectedAnswer').textContent = '';
    el('askMain').classList.remove('is-conversation');
    el('askWelcome').hidden = false; el('askSuggestions').hidden = false;
    el('conversationHeader').hidden = true; el('messages').hidden = true; el('sourcesPanel').hidden = true;
    el('askQuery').value = ''; el('askQuery').setCustomValidity(''); el('askQuery').focus();
    el('askQuery').placeholder = landingPlaceholder; updateCount();
    announce(busy ? 'Conversation cleared. Waiting for the previous request to finish.' : 'Conversation cleared.');
  });
  document.querySelectorAll('[data-starter]').forEach(button => {
    button.addEventListener('click', () => {
      el('askQuery').value = button.dataset.starter; el('askQuery').setCustomValidity(''); el('askQuery').focus();
      updateCount();
    });
  });
  mobile.addEventListener('change', placeSources);
  // Native modal dialog contains focus and makes the rest of the page inert.
  // The same controls form an anchored panel on desktop and a sheet on mobile.
  el('filterToggle').prepend(createUiIcon('filter'));
  el('filterClose').append(createUiIcon('close'));
  el('filterToggle').addEventListener('click', () => {
    el('askFilters').showModal();
    el('filterToggle').setAttribute('aria-expanded', 'true');
    positionFilters();
  });
  ['filterClose', 'filterDone'].forEach(id => el(id).addEventListener('click', () => el('askFilters').close()));
  el('askFilters').addEventListener('close', () => {
    el('filterToggle').setAttribute('aria-expanded', 'false');
    el('filterToggle').focus({preventScroll: true});
  });
  el('askFilters').addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const controls = [...el('askFilters').querySelectorAll('button, select')]
      .filter(control => !control.disabled && control.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first?.focus();
    }
  });
  el('filterReset').addEventListener('click', () => {
    el('askCollege').value = ''; el('askYear').value = ''; updateFilters();
  });
  ['askCollege', 'askYear'].forEach(id => el(id).addEventListener('change', updateFilters));
  window.addEventListener('resize', positionFilters);
  el('askButton').append(createUiIcon('arrow-right'));
  el('filterRetry').addEventListener('click', loadFilters);
  async function init() {
    if (!_getToken()) { location.replace('index.html'); return; }
    el('sessionRetry').hidden = true;
    try {
      const me = await apiMe();
      const name = (me.full_name || '').trim().split(/\s+/)[0];
      el('welcomeHeading').textContent = name ? 'Welcome back, ' + name : 'Welcome back';
      el('avatarEl').textContent = (me.full_name || me.username || '').slice(0, 2).toUpperCase();
      el('rolePill').textContent = me.role === 'librarian' ? 'Librarian' : 'Student';
      applyNav(me.role);
      el('askSession').textContent = ''; el('askForm').hidden = false; el('askSuggestions').hidden = false;
      await loadFilters();
    } catch (error) {
      el('askSession').textContent = error.message; el('sessionRetry').hidden = false;
    }
  }
  el('sessionRetry').addEventListener('click', init);
  init();
})();
