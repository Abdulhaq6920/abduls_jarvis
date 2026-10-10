// Long-term memory browser: search, add, delete, and a guarded wipe.
import { api } from '../api.js';
import { clear, el, timeAgo, toast } from '../dom.js';

export function mountMemory(root) {
  const list = el('div', { class: 'list' });
  const search = el('input', { type: 'text', maxLength: 200, placeholder: 'Search memories…', 'aria-label': 'Search memories' });
  const text = el('textarea', { maxLength: 2000, placeholder: 'e.g. My dentist appointment is on the 14th', 'aria-label': 'New memory' });
  const count = el('span', { class: 'muted' });

  async function load() {
    const q = search.value.trim();
    const items = await api(`/api/memory?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`);
    count.textContent = `${items.length} item${items.length === 1 ? '' : 's'}`;
    clear(list, items.length ? items.map((m) => el('div', { class: 'row spread' },
      el('div', { class: 'grow' }, el('div', { text: m.text }),
        el('div', { class: 'faint' }, `${m.source} · ${timeAgo(m.created_at)}`, m.tags.map((t) => el('span', { class: 'chip', style: 'margin-left:6px', text: t })))),
      el('button', { class: 'btn small danger', type: 'button', 'aria-label': 'Delete memory', onClick: async () => {
        try { await api(`/api/memory/${m.id}`, { method: 'DELETE' }); await load(); } catch (e) { toast(e.message, 'error'); } } }, 'Delete')))
      : el('div', { class: 'empty', text: q ? 'No matching memories.' : 'Nothing remembered yet. Say “remember that …” or add one here.' }));
  }

  async function add() {
    const value = text.value.trim(); if (!value) return;
    try { await api('/api/memory', { method: 'POST', body: { text: value } }); text.value = ''; toast('Remembered', 'ok'); await load(); }
    catch (e) { toast(e.message, 'error'); }
  }

  async function wipe() {
    if (!window.confirm('Permanently delete ALL memories? This cannot be undone.')) return;
    try { const r = await api('/api/memory?confirm=DELETE-ALL', { method: 'DELETE' }); toast(`Erased ${r.removed} memories`, 'ok'); await load(); }
    catch (e) { toast(e.message, 'error'); }
  }

  let debounce;
  search.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(() => load().catch(() => {}), 250); });

  root.append(el('div', { class: 'grid cols-2' },
    el('div', { class: 'card' }, el('div', { class: 'row spread' }, el('h3', { text: 'Stored memories' }), count),
      search, el('div', { style: 'margin-top:10px' }, list)),
    el('div', { class: 'stack' },
      el('div', { class: 'card' }, el('h3', { text: 'Add a memory' }), text,
        el('div', { class: 'row', style: 'margin-top:10px' }, el('button', { class: 'btn primary', type: 'button', onClick: add }, 'Remember'))),
      el('div', { class: 'card' }, el('h3', { text: 'Privacy' }),
        el('p', { class: 'muted', text: 'Memories are encrypted at rest and visible only to admin sign-ins. Conversation history is deleted automatically after the retention period.' }),
        el('button', { class: 'btn danger', type: 'button', onClick: wipe }, 'Erase all memories')))));
  load().catch((e) => toast(e.message, 'error'));
  return { onEvent: () => {}, destroy: () => {} };
}
