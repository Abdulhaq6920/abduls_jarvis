import {
  Bot,
  BrainCircuit,
  LayoutDashboard,
  LogOut,
  Moon,
  ShieldCheck,
  Sun,
  Server,
  type LucideIcon,
} from 'lucide-react';
import { Suspense, lazy, type ReactNode } from 'react';
import { useAuth } from '@/app/auth-context';
import { useLive } from '@/app/live-context';
import { useTheme } from '@/app/theme';
import { useHashRoute, type RouteId } from '@/app/useHashRoute';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Spinner } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/Button';
import { useApprovals } from '@/lib/queries';

const OverviewPage = lazy(() => import('@/features/overview/OverviewPage'));
const AgentsPage = lazy(() => import('@/features/agents/AgentsPage'));
const MemoryPage = lazy(() => import('@/features/memory/MemoryPage'));
const ApprovalsPage = lazy(() => import('@/features/approvals/ApprovalsPage'));
const SystemPage = lazy(() => import('@/features/system/SystemPage'));

interface NavItem {
  id: RouteId;
  label: string;
  sub: string;
  icon: LucideIcon;
}

const NAV: NavItem[] = [
  { id: 'overview', label: 'Overview', sub: 'Voice console and live status', icon: LayoutDashboard },
  { id: 'agents', label: 'Agents', sub: 'Roster, voices and activity', icon: Bot },
  { id: 'memory', label: 'Memory', sub: 'What JARVIS remembers', icon: BrainCircuit },
  { id: 'approvals', label: 'Approvals', sub: 'Actions waiting for you', icon: ShieldCheck },
  { id: 'system', label: 'System', sub: 'Health, audit and security', icon: Server },
];

const PAGES: Record<RouteId, ReactNode> = {
  overview: <OverviewPage />,
  agents: <AgentsPage />,
  memory: <MemoryPage />,
  approvals: <ApprovalsPage />,
  system: <SystemPage />,
};

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true" />
      <span className="brand-name">JARVIS</span>
    </div>
  );
}

export function Shell() {
  const [route, navigate] = useHashRoute();
  const { username, signOut } = useAuth();
  const { connected } = useLive();
  const { theme, toggle } = useTheme();
  const approvals = useApprovals();
  const pending = approvals.data?.filter((a) => a.status === 'PENDING').length ?? 0;
  const current = NAV.find((item) => item.id === route) ?? NAV[0]!;

  return (
    <div className="shell">
      <a className="skip-link" href="#content">
        Skip to content
      </a>

      <aside className="side">
        <Brand />
        <nav className="nav" aria-label="Main">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" aria-current={route === id ? 'page' : undefined} onClick={() => navigate(id)}>
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
              {id === 'approvals' && pending > 0 && <span className="count" aria-label={`${pending} pending`}>{pending}</span>}
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <div className="live" role="status">
            <span className={`dot ${connected ? 'dot-ok' : 'dot-off'}`} aria-hidden="true" />
            {connected ? 'Live updates on' : 'Reconnecting…'}
          </div>
          <div className="who">{username}</div>
        </div>
      </aside>

      <div className="main-col">
        <header className="topbar">
          <div className="topbar-title">
            <div className="mobile-brand">
              <Brand />
            </div>
            <h1>{current.label}</h1>
            <p>{current.sub}</p>
          </div>
          <div className="topbar-actions">
            <span className={`live-pill ${connected ? 'on' : ''}`} aria-hidden="true">
              <span className={`dot ${connected ? 'dot-ok' : 'dot-off'}`} />
              {connected ? 'Live' : 'Offline'}
            </span>
            <IconButton label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'} onClick={toggle}>
              {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
            </IconButton>
            <IconButton label="Sign out" onClick={() => void signOut()}>
              <LogOut size={18} aria-hidden="true" />
            </IconButton>
          </div>
        </header>

        <main id="content" className="content" tabIndex={-1}>
          <ErrorBoundary key={route}>
            <Suspense fallback={<Spinner />}>
              <div className="page" key={route}>
                {PAGES[route]}
              </div>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>

      <nav className="tabbar" aria-label="Main">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" aria-current={route === id ? 'page' : undefined} onClick={() => navigate(id)}>
            <span className="tabbar-icon">
              <Icon size={21} aria-hidden="true" />
              {id === 'approvals' && pending > 0 && <span className="count" aria-label={`${pending} pending`}>{pending}</span>}
            </span>
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
