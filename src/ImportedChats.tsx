import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import * as api from "./api";
import type { ConversationSummary } from "./types";

type Props = { query: string; revision: string; grouped?: boolean; renderRows: (items: ConversationSummary[]) => ReactNode };

export function ImportedChats({ query, revision, grouped = false, renderRows }: Props) {
  const [page, setPage] = useState<api.ImportedConversationPage | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  useEffect(() => {
    const request = ++generation.current;
    pending.current = true;
    setPage(null);
    setBusy(true);
    setError("");
    void api.listImportedConversations(query, 0, 100).then(result => {
      if (generation.current === request) setPage(result);
    }).catch(cause => { if (generation.current === request) setError(String(cause)); })
      .finally(() => { if (generation.current === request) { pending.current = false; setBusy(false); } });
    return () => { generation.current += 1; pending.current = false; };
  }, [query, revision, retry]);
  const more = async () => {
    if (!page || pending.current || page.offset + page.limit >= page.total) return;
    const request = generation.current;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.listImportedConversations(query, page.offset + page.limit, 100);
      if (generation.current !== request) return;
      setPage(previous => previous ? { ...result, conversations: Array.from(new Map(
        [...previous.conversations, ...result.conversations].map(item => [item.id, item]),
      ).values()) } : result);
    } catch (cause) { if (generation.current === request) setError(String(cause)); }
    finally { if (generation.current === request) { pending.current = false; setBusy(false); } }
  };
  if (grouped && page?.total === 0 && !busy && !error) return null;
  return <section className={grouped ? "conversation-group imported-chat-group" : "imported-chat-library"} aria-label="Imported chats">
    {grouped ? <button className="conversation-group-heading" aria-label={`Imported group, ${page?.total ?? 0}`} aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>
      <ChevronDown size={14} /><strong>Imported</strong><span>{page?.total ?? 0}</span>
    </button> : <p className="imported-chat-count">{page ? `${page.total.toLocaleString()} imported chats` : "Reading imported chats…"}</p>}
    {!grouped || !collapsed ? <div className={grouped ? "conversation-group-body" : undefined}>
      {page ? renderRows(page.conversations) : null}
      {busy ? <p className="imported-chat-status" role="status">{page ? "Loading more chats…" : "Reading imported chats…"}</p> : null}
      {error ? <div className="imported-chat-error" role="alert"><p>{error}</p><button onClick={() => page ? void more() : setRetry(value => value + 1)}>Try again</button></div> : null}
      {page?.total === 0 && !busy ? <div className="empty-state"><strong>{query ? "No imported chats match" : "No imported chats yet"}</strong><span>{query ? "Try another search." : "Choose Import chats to copy an export here."}</span></div> : null}
      {page && page.offset + page.limit < page.total ? <button className="imported-chat-more" aria-label="Load more imported chats" disabled={busy} onClick={() => void more()}>Load more · {page.conversations.length.toLocaleString()} of {page.total.toLocaleString()}</button> : null}
    </div> : null}
  </section>;
}
