import { QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { useAuth } from '@/app/auth-context';
import { AuthProvider } from '@/app/AuthProvider';
import { LiveProvider } from '@/app/LiveProvider';
import { ToastProvider } from '@/app/ToastProvider';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Shell } from '@/components/layout/Shell';
import LoginPage from '@/features/auth/LoginPage';
import { createQueryClient } from '@/lib/queryClient';

function Gate() {
  const { status } = useAuth();

  if (status === 'loading') {
    return (
      <div className="splash" role="status" aria-label="Loading JARVIS">
        <span className="splash-core" aria-hidden="true" />
      </div>
    );
  }
  if (status === 'anonymous') return <LoginPage />;
  return (
    <LiveProvider>
      <Shell />
    </LiveProvider>
  );
}

export default function App() {
  const [queryClient] = useState(createQueryClient);
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <AuthProvider>
            <Gate />
          </AuthProvider>
        </ToastProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
