import type * as React from 'react';

export function createAppUpdatePreferences(react: typeof React): {
  UpdatePreferences: React.ComponentType;
  markUpdateAttempt: (version: string | null | undefined) => void;
  useUpdateSchedule: (state: {
    version: string | null | undefined;
    blocked: boolean;
    busy: boolean;
    install: () => Promise<void>;
  }) => string;
};
