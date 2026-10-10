import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AuthContext, type AuthContextValue } from '@/app/auth-context';
import LoginPage from './LoginPage';

function renderLogin(overrides: Partial<AuthContextValue> = {}) {
  const value: AuthContextValue = {
    status: 'anonymous',
    username: null,
    notice: '',
    signIn: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(
    <AuthContext.Provider value={value}>
      <LoginPage />
    </AuthContext.Provider>,
  );
  return value;
}

describe('LoginPage', () => {
  it('submits trimmed credentials', async () => {
    const auth = renderLogin();
    await userEvent.type(screen.getByLabelText('Username'), '  owner ');
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(auth.signIn).toHaveBeenCalledWith('owner', 'correct horse');
  });

  it('shows the server error and clears the password', async () => {
    renderLogin({ signIn: vi.fn().mockRejectedValue(new Error('Invalid username or password.')) });
    await userEvent.type(screen.getByLabelText('Username'), 'owner');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password.');
    await waitFor(() => expect(screen.getByLabelText('Password')).toHaveValue(''));
  });

  it('explains an involuntary sign-out', () => {
    renderLogin({ notice: 'Your session has ended. Please sign in again.' });
    expect(screen.getByRole('alert')).toHaveTextContent('session has ended');
  });

  it('toggles password visibility', async () => {
    renderLogin();
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('type', 'password');
    await userEvent.click(screen.getByRole('button', { name: /show password/i }));
    expect(password).toHaveAttribute('type', 'text');
  });
});
