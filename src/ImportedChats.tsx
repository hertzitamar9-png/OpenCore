import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import * as api from "./api";
import type { ConversationSummary } from "./types";

type Props = {
  query: string; revision: string; grouped?: boolean;
  source?: api.ImportedConversationSource; label?: string; nativeItems?: ConversationSummary[];
  collapsed?: boolean; onToggle?: () => void;
  renderRows: (items: ConversationSummary[]) => ReactNode;
};

export function ImportedChats({ query, revision, grouped = false, source = "all", label = "Imported", nativeItems = [], collapsed: controlledCollapsed, onToggle, renderRows }: Props) {
  const [page, setPage] = useState<api.ImportedConversationPage | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const isCollapsed = controlledCollapsed ?? collapsed;
  // Keep the all-imports call compatible with existing callers; source categories
  // are filtered by the backend before their count and page are selected.
  const readPage = (offset: number) => source === "all"
    ? api.listImportedConversations(query, offset, 100)
    : api.listImportedConversations(query, offset, 100, source);
  useEffect(() => {
    const request = ++generation.current;
    pending.current = true;
    setPage(null);
    setBusy(true);
    setError("");
    void readPage(0).then(result => {
      if (generation.current === request) setPage(result);
    }).catch(cause => { if (generation.current === request) setError(String(cause)); })
      .finally(() => { if (generation.current === request) { pending.current = false; setBusy(false); } });
    return () => { generation.current += 1; pending.current = false; };
  }, [query, revision, retry, source]);
  const more = async () => {
    if (!page || pending.current || page.offset + page.limit >= page.total) return;
    const request = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await readPage(page.offset + page.limit);
      if (generation.current !== request) return;
      setPage(previous => previous ? { ...result, conversations: Array.from(new Map(
        [...previous.conversations, ...result.conversations].map(item => [item.id, item]),
      ).values()) } : result);
    } catch (cause) { if (generation.current === request) setError(String(cause)); }
    finally { if (generation.current === request) { pending.current = false; setBusy(false); } }
  };
  const total = nativeItems.length + (page?.total ?? 0);
  const chatLabel = label === "Imported" ? "imported" : label;
  const readingLabel = `Reading ${chatLabel} chats…`;
  const conversations = nativeItems.length ? [...nativeItems, ...(page?.conversations ?? [])].sort((left, right) =>
    Number(right.pinned) - Number(left.pinned) || right.updatedAt.localeCompare(left.updatedAt) || left.id.localeCompare(right.id)
  ) : page?.conversations ?? [];
  if (grouped && page?.total === 0 && !nativeItems.length && !busy && !error) return null;
  return <section className={grouped ? `conversation-group imported-chat-group ${isCollapsed ? "collapsed" : ""}` : "imported-chat-library"} aria-label={`${label} chats`}>
    {grouped ? <button className="conversation-group-heading" aria-label={`${label} group, ${total}`} aria-expanded={!isCollapsed} onClick={() => onToggle ? onToggle() : setCollapsed(value => !value)}>
      <ChevronDown size={14} /><strong>{label}</strong><span>{total}</span>
    </button> : <p className="imported-chat-count">{page ? `${total.toLocaleString()} ${chatLabel} chats` : readingLabel}</p>}
    {!grouped || !isCollapsed ? <div className={grouped ? "conversation-group-body" : undefined}>
      {conversations.length ? renderRows(conversations) : null}
      {busy ? <p className="imported-chat-status" role="status">{page ? "Loading more chats…" : readingLabel}</p> : null}
      {error ? <div className="imported-chat-error" role="alert"><p>{error}</p><button onClick={() => page ? void more() : setRetry(value => value + 1)}>Try again</button></div> : null}
      {page?.total === 0 && !nativeItems.length && !busy ? <div className="empty-state"><strong>{query ? `No ${chatLabel} chats match` : `No ${chatLabel} chats yet`}</strong><span>{query ? "Try another search." : "Choose Import chats to copy an export here."}</span></div> : null}
      {page && page.offset + page.limit < page.total ? <button className="imported-chat-more" aria-label={`Load more ${label.toLowerCase()} chats`} disabled={busy} onClick={() => void more()}>Load more · {page.conversations.length.toLocaleString()} of {page.total.toLocaleString()}</button> : null}
    </div> : null}
  </section>;
}
