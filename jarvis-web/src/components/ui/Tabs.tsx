import { useId, type KeyboardEvent, type ReactNode } from 'react';

export interface TabDef<T extends string> {
  id: T;
  label: string;
  icon?: ReactNode;
}

interface TabsProps<T extends string> {
  tabs: Array<TabDef<T>>;
  value: T;
  onChange: (id: T) => void;
  label: string;
}

/** WAI-ARIA tabs with arrow-key navigation. The caller renders the matching panel. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: TabsProps<T>) {
  const base = useId();

  const onKey = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = tabs[(index + step + tabs.length) % tabs.length];
    if (next) {
      onChange(next.id);
      document.getElementById(`${base}-${next.id}`)?.focus();
    }
  };

  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          id={`${base}-${tab.id}`}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          tabIndex={value === tab.id ? 0 : -1}
          onClick={() => onChange(tab.id)}
          onKeyDown={(e) => onKey(e, index)}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}
