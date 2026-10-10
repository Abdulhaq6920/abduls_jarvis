import { CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
import { ToastContext, type ToastApi, type ToastKind } from './toast-context';

interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
}

const ICONS = { ok: CheckCircle2, error: TriangleAlert, info: Info } as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setItems((current) => current.filter((t) => t.id !== id)), []);

  const notify = useCallback<ToastApi['notify']>(
    (message, kind = 'info') => {
      const id = nextId.current++;
      setItems((current) => [...current.slice(-3), { id, message, kind }]);
      setTimeout(() => dismiss(id), kind === 'error' ? 7000 : 4000);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {items.map((toast) => {
          const Icon = ICONS[toast.kind];
          return (
            <div key={toast.id} className={`toast toast-${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>
              <Icon size={18} aria-hidden="true" />
              <span>{toast.message}</span>
              <button type="button" className="icon-btn" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}>
                <X size={16} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
