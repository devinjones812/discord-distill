// Collects messages from the open channel into window.__scr (drained by drain.js).
// Optional window.__cfg = {start, end} (ISO UTC strings):
//   no cfg      -> scroll up to the start of the channel (full history)
//   cfg.start   -> scroll up until a message older than start is loaded
//   cfg.end     -> then scroll down until a message newer than end is loaded, or the latest message
(() => {
  if (window.__scr && window.__scr.running) return 'already running';
  const cfg = window.__cfg || {};
  window.__cfg = null;  // a later run without cfg must not reuse it
  const S = window.__scr = { running: true, msgs: {}, queue: [], idle: 0, iters: 0, done: false,
                             hitStart: false, reachedStart: false, reachedEnd: false };
  const grab = () => {
    let added = 0;
    for (const li of document.querySelectorAll('li[id^="chat-messages-"]')) {
      const mid = li.id.split('-').pop();
      if (S.msgs[mid]) continue;
      const u = document.getElementById('message-username-' + mid);
      const name = u ? (u.querySelector('[class*="username"]')?.innerText || u.innerText.split('\n')[0]).replace(/\s*\[.*$/, '').trim() : '';
      const t = li.querySelector('time[id^="message-timestamp-' + mid + '"]') || li.querySelector('h3 time, time');
      // A reply preview repeats the replied-to message's content element (same id), so skip previews.
      const c = [...li.querySelectorAll('[id="message-content-' + mid + '"]')].find(e => !e.closest('[id^="message-reply-context-"]'));
      const acc = document.getElementById('message-accessories-' + mid);
      const rc = document.getElementById('message-reply-context-' + mid);
      const urls = acc ? [...new Set([...acc.querySelectorAll('a[href], img[src], video[src]')].map(e => e.href || e.src).filter(x => x && !x.startsWith('data:')))] : [];
      S.queue.push(S.msgs[mid] = {
        id: mid, ts: t?.getAttribute('datetime') || '', author: name,
        content: c ? c.innerText : '',
        reply_to: rc ? rc.innerText.replace(/\n/g, ' ').slice(0, 200) : '',
        reply_to_id: rc?.querySelector('[id^="message-content-"]')?.id.split('-').pop() || '',
        edited: !!li.querySelector('[class*="edited"]'),
        attachments: urls.join(' '), embed_text: acc ? acc.innerText.trim() : ''
      });
      added++;
    }
    return added;
  };
  const times = () => Object.values(S.msgs).map(m => m.ts).filter(Boolean).sort();
  const ol = document.querySelector('ol[data-list-id="chat-messages"]');
  let sc = ol; while (sc && !(sc.scrollHeight > sc.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // The channel-start header is a non-message child of the list; message text can't trigger it.
  const atStart = () => [...ol.children].some(e => !e.matches('li[id^="chat-messages-"]')
    && /This is the start of the #|Welcome to #/.test(e.innerText.slice(0, 500)));
  const edgeId = which => { const l = document.querySelectorAll('li[id^="chat-messages-"]'); return l[which === 'first' ? 0 : l.length - 1]?.id; };
  // Scrolls one step in `dir` and waits up to 4 s for new messages at that edge.
  const step = async dir => {
    const edge = dir === 'up' ? 'first' : 'last', before = edgeId(edge);
    sc.scrollTop = dir === 'up' ? 0 : sc.scrollHeight;
    sc.dispatchEvent(new WheelEvent('wheel', { deltaY: dir === 'up' ? -1000 : 1000, bubbles: true }));
    for (let t = 0; t < 4000 && edgeId(edge) === before; t += 150) await sleep(150);
  };
  (async () => {
    // up
    S.idle = 0;
    while (S.running) {
      S.idle = grab() ? 0 : S.idle + 1; S.iters++;
      if (atStart()) { grab(); S.hitStart = S.reachedStart = true; break; }
      if (cfg.start && (times()[0] || '9') < cfg.start) { S.reachedStart = true; break; }
      if (S.idle >= 8) break;
      await step('up');
    }
    // down
    if (cfg.end) {
      S.idle = 0;
      while (S.running) {
        S.idle = grab() ? 0 : S.idle + 1; S.iters++;
        const t = times();
        if (t.length && t[t.length - 1] > cfg.end) { S.reachedEnd = true; break; }
        // at the latest message nothing new loads; 3 empty steps is enough
        if (S.idle >= 3) { S.reachedEnd = true; break; }
        await step('down');
      }
    }
    S.running = false; S.done = true;
  })();
  return 'started';
})()
