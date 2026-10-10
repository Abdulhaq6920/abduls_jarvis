import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Tabs } from './Tabs';

function Harness() {
  const [value, setValue] = useState<'a' | 'b' | 'c'>('a');
  return (
    <Tabs
      label="Sections"
      value={value}
      onChange={setValue}
      tabs={[
        { id: 'a', label: 'Alpha' },
        { id: 'b', label: 'Beta' },
        { id: 'c', label: 'Gamma' },
      ]}
    />
  );
}

describe('Tabs', () => {
  it('moves selection with the arrow keys and wraps around', async () => {
    render(<Harness />);
    const alpha = screen.getByRole('tab', { name: 'Alpha' });
    alpha.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Beta' })).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Gamma' })).toHaveAttribute('aria-selected', 'true');
  });

  it('only the selected tab is in the tab order', () => {
    render(<Harness />);
    expect(screen.getByRole('tab', { name: 'Alpha' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: 'Beta' })).toHaveAttribute('tabindex', '-1');
  });
});
