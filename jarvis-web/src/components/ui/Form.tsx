import { useId, type InputHTMLAttributes, type ReactNode } from 'react';

interface FieldProps {
  label: string;
  hint?: string;
  right?: ReactNode;
  children: (id: string) => ReactNode;
}

/** Label + control wrapper. The control receives a generated id so the label is associated. */
export function Field({ label, hint, right, children }: FieldProps) {
  const id = useId();
  return (
    <div className="field">
      <div className="field-top">
        <label htmlFor={id}>{label}</label>
        {right}
      </div>
      {children(id)}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'onChange'> {
  label: string;
  onChange: (checked: boolean) => void;
}

export function Switch({ label, onChange, className = '', ...rest }: SwitchProps) {
  return (
    <label className={`switch ${className}`.trim()}>
      <span className="switch-label">{label}</span>
      <input type="checkbox" role="switch" onChange={(e) => onChange(e.target.checked)} {...rest} />
      <span className="switch-track" aria-hidden="true" />
    </label>
  );
}
