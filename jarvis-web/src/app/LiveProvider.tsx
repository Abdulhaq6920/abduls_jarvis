import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { openEvents } from '@/lib/events';
import { keys } from '@/lib/queries';
import { LiveContext } from './live-context';

/** Opens the event stream while signed in and turns server events into cache invalidations. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const close = openEvents(
      (event) => {
        if (event.type === 'runtime') {
          void queryClient.invalidateQueries({ queryKey: keys.runtime });
        } else if (event.type === 'approval') {
          void queryClient.invalidateQueries({ queryKey: keys.approvals });
          void queryClient.invalidateQueries({ queryKey: keys.status });
        } else if (event.type === 'presence') {
          void queryClient.invalidateQueries({ queryKey: keys.agents });
        }
      },
      setConnected,
    );
    return close;
  }, [queryClient]);

  const value = useMemo(() => ({ connected }), [connected]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
