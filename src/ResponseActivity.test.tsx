import { fireEvent, render, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ResponseActivity } from './AssistantConversation';
import type { TimelineEntry } from './types';

function row(id: number, kind: string, content: string, title = ''): TimelineEntry {
  return { id, conversationId: 'preview', timestamp: '2026-10-07T18:00:00Z',
    kind, role: kind === 'tool_result' ? 'tool' : 'assistant', source: 'OpenCore', title, content, metadata: {} };
}

it('previews the model explanation for each step and retains its full text', () => {
  const explanation = 'The microphone worker loads the packed checkpoint successfully, but importing the CUDA runtime dominates startup. I will compare the CPU-only path against the same recording before changing the default implementation.\n\nThe accuracy check must pass before the replacement is shipped.';
  const { container } = render(<ResponseActivity active={false} events={[
    row(1, 'thinking', explanation),
    row(2, 'tool_call', '{"action":"read","path":"speech/phonon_original.py"}', 'dev'),
    row(3, 'tool_result', '{}', 'dev'),
    row(4, 'thinking', 'The measured transcript matches. Next I will check the startup and RAM measurements.'),
  ]} />);
  const disclosures = container.querySelectorAll<HTMLDetailsElement>('.kind-thinking');
  expect(disclosures).toHaveLength(2);
  expect(disclosures[0].querySelector('summary')).toHaveTextContent('importing the CUDA runtime dominates startup');
  expect(disclosures[1].querySelector('summary')).toHaveTextContent('The measured transcript matches');
  expect(container).not.toHaveTextContent('check what happened');
  fireEvent.click(disclosures[0].querySelector('summary')!);
  const paragraphs = Array.from(disclosures[0].querySelectorAll('.reasoning-text p'), p => p.textContent);
  expect(paragraphs).toEqual(explanation.split('\n\n'));
  expect(within(container).getByText('Used 1 tool')).toBeInTheDocument();
});

it('renders emphasis and lists in the available reasoning text', () => {
  const { container } = render(<ResponseActivity active events={[row(1, 'thinking', '**Checking the capture**\n\n- Permission is required.\n- Preserve the app settings.')]} />);
  expect(container.querySelector('.reasoning-text strong')).toHaveTextContent('Checking the capture');
  expect(container.querySelectorAll('.reasoning-text li')).toHaveLength(2);
  expect(container.querySelector('.reasoning-text')).not.toHaveTextContent('**');
});

it('does not invent a model explanation when no reasoning text was supplied', () => {
  const { container } = render(<ResponseActivity active={false} events={[row(1, 'thinking', '')]} />);
  expect(container.querySelector('summary')).toHaveTextContent('No reasoning text was supplied for this step.');
});
