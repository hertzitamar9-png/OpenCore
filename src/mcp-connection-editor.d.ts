import type { ReactNode } from "react";
import type { McpServer } from "./agent-platform";

export function McpConnectionEditor(props: {
  servers: McpServer[] | null;
  disabled: boolean;
  value: string;
  onChange: (text: string) => void;
  error: string;
  parse: (text: string) => McpServer[];
}): ReactNode;
