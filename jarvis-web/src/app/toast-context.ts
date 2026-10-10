import { createContext, useContext } from 'react';

export type ToastKind = 'info' | 'ok' | 'error';

export interface ToastApi {
  notify: (message: string, kind?: ToastKind) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside <ToastProvider>');
  return value;
}
