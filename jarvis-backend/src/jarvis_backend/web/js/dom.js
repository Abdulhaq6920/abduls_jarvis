// Tiny DOM helpers. SECURITY: everything is built with createElement/textContent. There is
// deliberately no innerHTML anywhere in this app, so untrusted text (utterances, memories,
// agent output) can never be interpreted as markup.

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'style') applyStyle(node, value);
    else if (key === 'for') node.htmlFor = value;
    else if (key in node && key !== 'list') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// CSP forbids inline styles set through the `style` attribute or `cssText`, but allows
// per-property CSSOM writes. So "a:b;c:d" is applied one declaration at a time.
function applyStyle(node, value) {
  const pairs = typeof value === 'string'
    ? value.split(';').map((part) => part.split(/:(.*)/s).slice(0, 2)).filter((p) => p[0]?.trim() && p[1]?.trim())
    : Object.entries(value);
  for (const [name, val] of pairs) node.style.setProperty(name.trim(), String(val).trim());
}

export function clear(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter(Boolean));
  return node;
}

export function timeAgo(iso) {
  if (!iso) return 'never';
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');
export const initial = (name) => (name || '?').trim().charAt(0).toUpperCase();
export const pretty = (id) => id.replace(/_/g, ' ');

export function toast(message, kind = '') {
  let host = document.querySelector('.toasts');
  if (!host) { host = el('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(host); }
  const node = el('div', { class: `toast ${kind}` }, message);
  host.append(node);
  setTimeout(() => node.remove(), kind === 'error' ? 6000 : 3500);
}
