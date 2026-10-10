# jarvis-web

The JARVIS dashboard: React 19 + TypeScript + Vite. It is served by the FastAPI backend under
`/ui/`, so it is same-origin with the API (no CORS) and covered by the backend's strict CSP.

## Develop

```powershell
# terminal 1: the backend (from the repo root)
python -m jarvis_backend serve

# terminal 2: the dashboard with hot reload, proxying /api and /ws to the backend
cd jarvis-web
npm ci
npm run dev            # http://localhost:5173/ui/
```

`npm run check` runs typecheck, lint, unit tests and a production build.

## Build for the backend

```powershell
npm run build
```

Output goes to `jarvis-backend/src/jarvis_backend/web/` (generated and git-ignored). The Docker image
builds it in a Node stage, so the runtime image contains static files only, never Node.
Without a build the API still works and `/` answers 503 with instructions.

## Layout

```
src/
  app/          providers: auth, toasts, live events (WebSocket), theme, hash router
  components/   layout/Shell, VoiceOrb, ErrorBoundary, ui/ (Button, Card, Dialog, Tabs, Form)
  features/     auth, overview, agents, memory, approvals, system (one folder per page)
  lib/          api client + session, typed query hooks, formatting, speech, wire types
  styles/       tokens (dark/light), base, layout, components, orb, pages
```

## Security rules the code follows (and tests enforce)

* The access token lives in memory only. The refresh token is an HttpOnly cookie JavaScript cannot read.
* Refresh is single-flight, so parallel 401s never replay a rotating token.
* No `dangerouslySetInnerHTML`, no `innerHTML`: untrusted text is always rendered as text.
* The CSP forbids inline script and style. Fonts are self-hosted, nothing loads from a CDN, and
  the one pre-paint script (`public/theme-init.js`) is an external file.
* Only the theme preference is stored in the browser.

## Accessibility

Skip link, landmarks, labelled controls, visible focus, keyboard-navigable tabs, native `<dialog>`
modals, `aria-live` toasts, and `prefers-reduced-motion` support. Full WCAG validation needs manual
testing with assistive technologies.
