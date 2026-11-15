/* The server owns permission checks and watermarking. Never fall back to raw PDFs. */
class WatermarkedReader {
  constructor(container, researchId) {
    this.container = container;
    this.researchId = researchId;
    this.closed = false;
    this.urls = new Set();
    this.queue = [];
    this.running = 0;
    this.abort = new AbortController();
    this.blockAction = event => event.preventDefault();
    this.blockShortcut = event => {
      if (!this.closed && container.getClientRects().length && (event.ctrlKey || event.metaKey) && ['p', 's'].includes(event.key.toLowerCase())) {
        event.preventDefault();
      }
    };
    container.addEventListener('contextmenu', this.blockAction);
    container.addEventListener('dragstart', this.blockAction);
    document.addEventListener('keydown', this.blockShortcut);
    this.loadInfo();
  }

  message(text, retry) {
    const p = document.createElement('p');
    p.textContent = text; p.setAttribute('role', 'status');
    this.container.replaceChildren(p);
    if (retry) this.container.appendChild(this.retryButton('Retry reader', retry));
  }

  retryButton(label, action) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn btn-ghost btn-sm'; button.textContent = label;
    button.addEventListener('click', action);
    return button;
  }

  async loadInfo() {
    this.message('Loading the watermarked reader…');
    try {
      const info = await apiReaderInfo(this.researchId);
      if (this.closed) return;
      if (!Number.isInteger(info.page_count) || info.page_count < 1 || !(info.width > 0) || !(info.height > 0) || info.watermark !== true) {
        throw new Error('Reader information is unavailable or incompatible. Please try again.');
      }
      this.container.replaceChildren();
      const count = document.createElement('p'); count.textContent = `${info.page_count} pages · Watermarked for your account`;
      this.container.appendChild(count);
      // At most two images are fetched together. Placeholders reserve page space;
      // the observer follows the paper panel's scroll without loading the whole PDF.
      this.observer = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (entry.isIntersecting && !entry.target.dataset.requested) {
            entry.target.dataset.requested = 'true';
            this.observer.unobserve(entry.target);
            this.queue.push(entry.target);
          }
        });
        this.pump();
      }, {rootMargin: '180px'});
      for (let n = 1; n <= info.page_count; n++) {
        const page = document.createElement('figure');
        page.className = 'reader-page'; page.dataset.page = n;
        page.style.aspectRatio = `${info.width} / ${info.height}`;
        page.setAttribute('aria-label', `Page ${n}`);
        const placeholder = document.createElement('p'); placeholder.textContent = `Page ${n} will load as you scroll.`;
        page.appendChild(placeholder); this.container.appendChild(page);
        this.observer.observe(page);
      }
    } catch (error) {
      if (this.closed) return;
      this.message(this.accessMessage(error), error.status === 403 || error.status === 401 ? null : () => this.loadInfo());
    }
  }

  accessMessage(error) {
    if (error.reason === 'not_approved') return 'This paper is not approved for public viewing.';
    if (error.reason === 'not_consented') return 'The author has not allowed public viewing of this paper.';
    return error.message;
  }

  pump() {
    while (!this.closed && this.running < 2 && this.queue.length) {
      const page = this.queue.shift();
      this.running++;
      this.loadPage(page).finally(() => { this.running--; this.pump(); });
    }
  }

  async loadPage(page) {
    const n = Number(page.dataset.page);
    page.textContent = `Loading page ${n}…`; page.setAttribute('aria-busy', 'true');
    try {
      const blob = await apiReaderPage(this.researchId, n, this.abort.signal);
      if (this.closed) return;
      const url = URL.createObjectURL(blob); this.urls.add(url);
      const image = document.createElement('img');
      image.alt = `Watermarked research page ${n}`;
      image.draggable = false; image.decoding = 'async'; image.src = url;
      image.addEventListener('error', () => {
        URL.revokeObjectURL(url); this.urls.delete(url);
        if (!this.closed) this.pageError(page, `Page ${n} could not be displayed.`);
      }, {once: true});
      page.replaceChildren(image);
    } catch (error) {
      if (this.closed || error.name === 'AbortError') return;
      if (error.status === 403 || error.status === 401) {
        // Access can change after opening; discard all previously displayed pages.
        this.release();
        this.message(this.accessMessage(error));
        return;
      }
      this.pageError(page, this.accessMessage(error));
    } finally { page.setAttribute('aria-busy', 'false'); }
  }

  pageError(page, message) {
    const text = document.createElement('p'); text.textContent = message; text.setAttribute('role', 'status');
    page.replaceChildren(text, this.retryButton(`Retry page ${page.dataset.page}`, () => {
      page.textContent = 'Waiting to retry…'; this.queue.push(page); this.pump();
    }));
  }

  release() {
    this.closed = true;
    this.observer?.disconnect(); this.queue = []; this.abort.abort();
    this.urls.forEach(url => URL.revokeObjectURL(url)); this.urls.clear();
    this.container.removeEventListener('contextmenu', this.blockAction);
    this.container.removeEventListener('dragstart', this.blockAction);
    document.removeEventListener('keydown', this.blockShortcut);
  }

  destroy() { this.release(); this.container.replaceChildren(); }
}
