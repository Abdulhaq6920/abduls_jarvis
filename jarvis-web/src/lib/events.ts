import { session } from './api';
import type { LiveEvent } from './types';

/**
 * Live event stream (WebSocket) with exponential-backoff reconnect.
 * The access token goes in the FIRST MESSAGE, never the URL (URLs end up in logs/history).
 * Returns a function that closes the connection for good.
 */
export function openEvents(onEvent: (event: LiveEvent) => void, onState: (connected: boolean) => void): () => void {
  let socket: WebSocket | null = null;
  let closed = false;
  let delay = 1000;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const connect = (): void => {
    const token = session.token;
    if (closed || !token) return;
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${scheme}://${location.host}/ws/events`);
    socket = ws;

    ws.onopen = () => {
      delay = 1000;
      ws.send(JSON.stringify({ token }));
    };
    ws.onmessage = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as LiveEvent;
        if (event.type === 'hello') onState(true);
        else if (event.type !== 'ping') onEvent(event);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      onState(false);
      if (!closed) {
        delay = Math.min(delay * 2, 15000);
        timer = setTimeout(connect, delay);
      }
    };
  };

  connect();
  return () => {
    closed = true;
    clearTimeout(timer);
    socket?.close();
  };
}
