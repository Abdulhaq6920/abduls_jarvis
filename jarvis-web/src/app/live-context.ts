import { createContext, useContext } from 'react';

export interface LiveContextValue {
  /** True while the WebSocket is authenticated and open. */
  connected: boolean;
}

export const LiveContext = createContext<LiveContextValue>({ connected: false });

export const useLive = (): LiveContextValue => useContext(LiveContext);
