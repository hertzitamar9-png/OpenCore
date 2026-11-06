import type * as React from 'react';
export function createComputerDriverPanel(react: typeof React, services: {
  request(path: string, body?: object): Promise<any>;
  loadConfiguration(): Promise<{mcpServers: any[]}>;
  saveServers(servers: any[]): Promise<unknown>;
}): React.ComponentType;
