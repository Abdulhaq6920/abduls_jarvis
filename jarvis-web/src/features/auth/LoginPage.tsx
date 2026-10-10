import { Eye, EyeOff, LogIn } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useAuth } from '@/app/auth-context';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Form';
import { errorMessage } from '@/lib/api';

export default function LoginPage() {
  const { signIn, notice } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await signIn(username.trim(), password);
    } catch (e) {
      setError(errorMessage(e));
      setPassword('');
      setBusy(false);
    }
  }

  const message = error || notice;

  return (
    <main className="login-wrap">
      <div className="login-aurora" aria-hidden="true" />
      <form className="login-card" onSubmit={(e) => void submit(e)} aria-labelledby="login-title">
        <div className="login-mark" aria-hidden="true">
          <span className="orb-ring r1" />
          <span className="orb-ring r2" />
          <span className="orb-core" />
        </div>
        <h1 id="login-title" className="login-title">
          JARVIS
        </h1>
        <p className="login-sub">Sign in to your control center</p>

        <Field label="Username">
          {(id) => (
            <input
              id={id}
              type="text"
              autoComplete="username"
              required
              maxLength={64}
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          )}
        </Field>

        <Field label="Password">
          {(id) => (
            <div className="input-affix">
              <input
                id={id}
                type={reveal ? 'text' : 'password'}
                autoComplete="current-password"
                required
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="icon-btn"
                aria-label={reveal ? 'Hide password' : 'Show password'}
                aria-pressed={reveal}
                onClick={() => setReveal((v) => !v)}
              >
                {reveal ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
              </button>
            </div>
          )}
        </Field>

        <div className="form-error" role="alert">
          {message}
        </div>

        <Button type="submit" variant="primary" loading={busy} icon={<LogIn size={18} aria-hidden="true" />} className="btn-block">
          Sign in
        </Button>
        <p className="login-foot">
          No account yet? Run <code>python -m jarvis_backend create-admin</code>
        </p>
      </form>
    </main>
  );
}
