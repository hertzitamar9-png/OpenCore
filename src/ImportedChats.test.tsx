import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

it("pages and searches within the selected source beyond the first hundred chats", async () => {
  const hermes = Array.from({length: 203}, (_, index) => chat(`Hermes notes ${index}`));
  const page = vi.spyOn(api, "listImportedConversations").mockImplementation(async (query = "", offset = 0, limit = 100, source = "all") => {
    const matches = source === "hermes" ? hermes.filter(item => item.title.toLowerCase().includes(query.toLowerCase())) : [];
    return {conversations: matches.slice(offset, offset + limit), total: matches.length, offset, limit};
  });
  const { rerender } = render(<ImportedChats query="" revision="0" source="hermes" label="Hermes" renderRows={rows} />);
  const region = screen.getByRole("region", {name: "Hermes chats"});
  await within(region).findByText("Hermes notes 99");
  expect(within(region).queryByText("Hermes notes 100")).not.toBeInTheDocument();
  fireEvent.click(within(region).getByRole("button", {name: "Load more hermes chats"}));
  await within(region).findByText("Hermes notes 199");
  fireEvent.click(within(region).getByRole("button", {name: "Load more hermes chats"}));
  await within(region).findByText("Hermes notes 202");
  expect(page).toHaveBeenCalledWith("", 100, 100, "hermes");
  expect(page).toHaveBeenCalledWith("", 200, 100, "hermes");
  expect(within(region).queryByRole("button", {name: "Load more hermes chats"})).not.toBeInTheDocument();
  rerender(<ImportedChats query="notes 202" revision="0" source="hermes" label="Hermes" renderRows={rows} />);
  await waitFor(() => expect(page).toHaveBeenCalledWith("notes 202", 0, 100, "hermes"));
  await within(region).findByText("Hermes notes 202");
  expect(within(region).queryByText("Hermes notes 99")).not.toBeInTheDocument();
  expect(within(region).getByText("1 Hermes chats")).toBeVisible();
});

it("discards a pending page when changing source with the same search", async () => {
  let complete!: (page: api.ImportedConversationPage) => void;
  const stale = new Promise<api.ImportedConversationPage>(resolve => { complete = resolve; });
  const page = vi.spyOn(api, "listImportedConversations").mockImplementation(async (_query = "", offset = 0, limit = 100, source = "all") => {
    if (source === "opencode") return {conversations: [chat("OpenCode current")], total: 1, offset, limit};
    if (offset > 0) return stale;
    return {conversations: [chat("Hermes old")], total: 101, offset, limit};
  });
  const { rerender } = render(<ImportedChats query="same" revision="0" source="hermes" label="Hermes" renderRows={rows} />);
  await screen.findByText("Hermes old");
  fireEvent.click(screen.getByRole("button", {name: "Load more hermes chats"}));
  rerender(<ImportedChats query="same" revision="0" source="opencode" label="OpenCode" renderRows={rows} />);
  await screen.findByText("OpenCode current");
  await act(async () => { complete({conversations: [chat("Late Hermes")], total: 101, offset: 100, limit: 100}); await stale; });
  expect(page).toHaveBeenCalledWith("same", 0, 100, "opencode");
  expect(screen.queryByText("Hermes old")).not.toBeInTheDocument();
  expect(screen.queryByText("Late Hermes")).not.toBeInTheDocument();
  expect(screen.getByText("OpenCode current")).toBeVisible();
});

it("combines native and copied chats under one collapsible source heading", async () => {
  const native = {...chat("Native Codex"),client:"Codex"};
  const copied = {...chat("Copied Codex"),client:"Imported Codex"};
  vi.spyOn(api,"listImportedConversations").mockResolvedValue({conversations:[copied],total:1,offset:0,limit:100});
  render(<ImportedChats query="" revision="0" source="codex" label="Codex" nativeItems={[native]} grouped renderRows={rows} />);
  const heading = await screen.findByRole("button",{name:"Codex group, 2"});
  expect(screen.getByText("Native Codex")).toBeVisible();
  expect(screen.getByText("Copied Codex")).toBeVisible();
  fireEvent.click(heading);
  expect(heading).toHaveAttribute("aria-expanded","false");
  expect(screen.queryByText("Native Codex")).not.toBeInTheDocument();
  expect(screen.queryByText("Copied Codex")).not.toBeInTheDocument();
});
