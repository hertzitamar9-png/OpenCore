import type * as React from 'react';

type NavigationItem<T extends string> = { id: T; label: string; group?: boolean; icon: React.ElementType<{ size?: number }> };
export function createNavigationItems<T extends string>(react: typeof React, groupEntry?: React.ComponentType<any>): React.ComponentType<{
  items: NavigationItem<T>[];
  active: T;
  onChange: (view: T) => void;
}>;
