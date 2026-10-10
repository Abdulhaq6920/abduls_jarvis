import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ErrorState } from './ui/Card';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Keeps one crashed page from blanking the whole dashboard. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // No remote error reporting is configured; the console is the only sink.
    console.error('UI error', error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error) {
      return <ErrorState error={this.state.error} onRetry={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}
