import { Brain, Plus, Search, Trash2 } from 'lucide-react';
import { useDeferredValue, useState, type FormEvent } from 'react';
import { useToast } from '@/app/toast-context';
import { Button } from '@/components/ui/Button';
import { Card, Chip, EmptyState, ErrorState, Skeleton } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/Dialog';
import { Field } from '@/components/ui/Form';
import { errorMessage } from '@/lib/api';
import { timeAgo } from '@/lib/format';
import { useAddMemory, useDeleteMemory, useMemories, useWipeMemories } from '@/lib/queries';

export default function MemoryPage() {
  const { notify } = useToast();
  const [search, setSearch] = useState('');
  const query = useDeferredValue(search.trim());
  const memories = useMemories(query);
  const add = useAddMemory();
  const remove = useDeleteMemory();
  const wipe = useWipeMemories();
  const [text, setText] = useState('');
  const [confirmWipe, setConfirmWipe] = useState(false);

  async function onAdd(event: FormEvent): Promise<void> {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
    try {
      await add.mutateAsync(value);
      setText('');
      notify('Remembered', 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  async function onDelete(id: string): Promise<void> {
    try {
      await remove.mutateAsync(id);
    } catch (error) {
      notify(errorMessage(error), 'error');
    }
  }

  async function onWipe(): Promise<void> {
    try {
      const result = await wipe.mutateAsync();
      notify(`Erased ${result.removed} ${result.removed === 1 ? 'memory' : 'memories'}`, 'ok');
    } catch (error) {
      notify(errorMessage(error), 'error');
    } finally {
      setConfirmWipe(false);
    }
  }

  const items = memories.data ?? [];

  return (
    <div className="grid cols-2 memory-layout">
      <Card
        title="Stored memories"
        action={<span className="muted">{memories.data ? `${items.length} item${items.length === 1 ? '' : 's'}` : ''}</span>}
      >
        <Field label="Search memories">
          {(id) => (
            <div className="input-affix">
              <Search size={17} aria-hidden="true" className="affix-icon" />
              <input id={id} type="search" className="with-icon" maxLength={200} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search memories…" />
            </div>
          )}
        </Field>
        <div className="memory-list">
          {memories.isLoading ? (
            <Skeleton lines={4} />
          ) : memories.isError ? (
            <ErrorState error={memories.error} onRetry={() => void memories.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState icon={<Brain size={26} aria-hidden="true" />}>
              {query ? 'No matching memories.' : 'Nothing remembered yet. Say “remember that …” or add one here.'}
            </EmptyState>
          ) : (
            <ul>
              {items.map((m) => (
                <li key={m.id} className="memory-item">
                  <div className="grow">
                    <p>{m.text}</p>
                    <div className="faint meta">
                      {m.source} · {timeAgo(m.created_at)}
                      {m.tags.map((t) => (
                        <Chip key={t}>{t}</Chip>
                      ))}
                    </div>
                  </div>
                  <Button size="sm" variant="danger" icon={<Trash2 size={14} aria-hidden="true" />} aria-label={`Delete memory: ${m.text.slice(0, 40)}`} onClick={() => void onDelete(m.id)} disabled={remove.isPending}>
                    Delete
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      <div className="stack">
        <Card title="Add a memory">
          <form className="stack" onSubmit={(e) => void onAdd(e)}>
            <Field label="New memory">
              {(id) => <textarea id={id} rows={4} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. My dentist appointment is on the 14th" />}
            </Field>
            <div>
              <Button type="submit" variant="primary" loading={add.isPending} disabled={!text.trim()} icon={<Plus size={16} aria-hidden="true" />}>
                Remember
              </Button>
            </div>
          </form>
        </Card>
        <Card title="Privacy">
          <p className="muted">
            Memories are encrypted at rest and visible only to admin sign-ins. Conversation history is deleted automatically
            after the retention period.
          </p>
          <Button variant="danger" icon={<Trash2 size={16} aria-hidden="true" />} onClick={() => setConfirmWipe(true)}>
            Erase all memories
          </Button>
        </Card>
      </div>

      <ConfirmDialog
        open={confirmWipe}
        title="Erase all memories?"
        message="This permanently deletes every stored memory. It cannot be undone."
        confirmLabel="Erase everything"
        danger
        busy={wipe.isPending}
        onConfirm={() => void onWipe()}
        onCancel={() => setConfirmWipe(false)}
      />
    </div>
  );
}
