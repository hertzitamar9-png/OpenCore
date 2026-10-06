import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "./api";
import { ImportedChats } from "./ImportedChats";
import type { ConversationSummary } from "./types";

const chat = (id: string): ConversationSummary => ({ id, title: id, client: "Imported Hermes", createdAt: "2020-01-01T00:00:00Z", updatedAt: "2020-01-01T00:00:00Z", profile: "history", status: "imported", messageCount: 2, project: "", pinned: false });
const rows = (items: ConversationSummary[]) => items.map(item => <p key={item.id}>{item.title}</p>);
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("does not append a late page from the previous search", async () => {
  let finishOldPage!: (page: api.ImportedConversationPage) => void;
  const oldPage = new Promise<api.ImportedConversationPage>(resolve => { finishOldPage = resolve; });
  vi.spyOn(api, "listImportedConversations").mockImplementation(async (query = "", offset = 0) => {
    if (query === "new search") return { conversations: [chat("Current search result")], total: 1, offset: 0, limit: 100 };
    if (offset > 0) return oldPage;
    return { conversations: Array.from({length: 100}, (_, index) => chat(`First page ${index}`)), total: 101, offset: 0, limit: 100 };
  });
  const { rerender } = render(<ImportedChats query="" revision="0" renderRows={rows} />);
  await screen.findByText("First page 99");
  fireEvent.click(screen.getByRole("button", {name: "Load more imported chats"}));
  rerender(<ImportedChats query="new search" revision="0" renderRows={rows} />);
  await screen.findByText("Current search result");
  await act(async () => { finishOldPage({conversations: [chat("Stale result")], total: 101, offset: 100, limit: 100}); await oldPage; });
  expect(screen.getByText("Current search result")).toBeVisible();
  expect(screen.queryByText("Stale result")).not.toBeInTheDocument();
  expect(screen.queryByText("First page 99")).not.toBeInTheDocument();
});
