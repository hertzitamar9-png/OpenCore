import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { EchoContextStatus } from './EchoContextStatus';
import * as api from './api';

it('shows prompt use, request capacity, and archived activity for the selected conversation', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({available:true,liveTokens:5600,promptTokens:7000,windowTokens:32768,compactions:3,offloadedMessages:18,warmCache:{budgetBytes:128*1024*1024,residentBytes:7.5*1024*1024,pages:12,hits:6,misses:2,evictions:1,oversized:0,hitRate:0.75}});
  try {
    render(<EchoContextStatus conversationId="game" running />);
    expect(await screen.findByText('3 compactions')).toBeVisible();
    expect(await screen.findByText('ECHO holds 18 archived messages')).toBeVisible();
    expect(screen.getByText('ECHO RAM cache · 7.5 / 128 MiB · 75% hits')).toBeVisible();
    expect(read).toHaveBeenCalledWith('game');
    expect(screen.getByText('7,000 last request / 32,768 tokens · 21%')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Rolling model window usage' })).toHaveAttribute('value', '7000');
  } finally { read.mockRestore(); }
});

it('shows persistent ECHO history and the model request capacity separately', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({available:true,liveTokens:5400,promptTokens:6200,windowTokens:262144,contextMode:'persistent_echo',compactions:0,offloadedMessages:12,active:false});
  try {
    render(<EchoContextStatus conversationId="game" running={false} />);
    expect(await screen.findByText('ECHO history saved · runtime stopped')).toBeVisible();
    expect(screen.getByText('5,400 retained across turns / 262,144 tokens · 2%')).toBeVisible();
    expect(screen.getByText('Exact conversation history stays in the ECHO archive for source retrieval')).toBeVisible();
    expect(screen.getByText('Last request · 6,200 tokens')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Rolling model window usage' })).toHaveAttribute('value', '5400');
  } finally { read.mockRestore(); }
});

it('shows real persistent model-session tokens and rolling slot occupancy', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({
    available: true,
    liveTokens: 9000,
    promptTokens: 12000,
    windowTokens: 262144,
    modelSessionTokens: 45000,
    modelActiveTokens: 3200,
    modelContextTokens: 4096,
    modelSessionActive: false,
    contextMode: 'persistent_echo',
    active: false,
  });
  try {
    render(<EchoContextStatus conversationId="game" running={false} />);
    expect(await screen.findByText('Live model context')).toBeVisible();
    expect(screen.getByText('45,000 tokens processed in this session · 3,200 / 4,096 in rolling window')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Rolling model window usage' })).toHaveAttribute('value', '3200');
    expect(screen.getByRole('progressbar', { name: 'Rolling model window usage' })).toHaveAttribute('max', '4096');
    expect(screen.getByText('Model state is reused between turns; at its limit ECHO rebuilds from the retained transcript')).toBeVisible();
    expect(screen.getByText('Model slot · ready')).toBeVisible();
  } finally { read.mockRestore(); }
});

it('shows attention KV placement separately from the ECHO RAM page cache', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({available:true,liveTokens:3000,windowTokens:32768,warmCache:{budgetBytes:128*1024*1024,residentBytes:0,pages:0,hits:0,misses:0,evictions:0,oversized:0,hitRate:0}});
  try {
    render(<EchoContextStatus conversationId="game" running attentionKvLocation="system RAM" attentionKvType="Q4_0" />);
    expect(await screen.findByText(/Attention KV configured · Q4_0 · system RAM/)).toBeVisible();
    expect(screen.getByText(/ECHO RAM cache · 0\.0 \/ 128 MiB/)).toBeVisible();
  } finally { read.mockRestore(); }
});

it('does not display an invalid negative auto-compact threshold from a prior run', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({available:true,liveTokens:14859,windowTokens:32768,autoCompactThreshold:-232,autoCompactEnabled:true});
  try {
    render(<EchoContextStatus conversationId="game" running={false} />);
    expect(await screen.findByText('Last prompt snapshot')).toBeVisible();
    expect(screen.queryByText(/Auto compact/)).not.toBeInTheDocument();
    expect(screen.queryByText(/-232/)).not.toBeInTheDocument();
  } finally { read.mockRestore(); }
});

it('reports SDK summary compaction disabled and hides its stale threshold', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({
    available:true,liveTokens:14859,windowTokens:32768,contextMode:'persistent_echo',
    autoCompactThreshold:200000,autoCompactEnabled:false,compactions:0,offloadedMessages:12,
  });
  try {
    render(<EchoContextStatus conversationId="game" running={false} />);
    expect(await screen.findByText('SDK summary compaction disabled')).toBeVisible();
    expect(screen.getByText('Exact conversation history stays in the ECHO archive for source retrieval')).toBeVisible();
    expect(screen.queryByText(/Auto compact/)).not.toBeInTheDocument();
    expect(screen.queryByText(/200,000/)).not.toBeInTheDocument();
  } finally { read.mockRestore(); }
});

it('reports unavailable telemetry instead of inventing zero usage', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockRejectedValue(new Error('offline'));
  try {
    render(<EchoContextStatus conversationId="game" running />);
    expect(await screen.findByText('Context telemetry reconnecting…')).toBeVisible();
    expect(screen.queryByText(/0 live history tokens/)).not.toBeInTheDocument();
  } finally { read.mockRestore(); }
});

it('loads the last saved context snapshot while the model runtime is stopped', async () => {
  const read = vi.spyOn(api, 'echoWorkingSet').mockResolvedValue({available:true,liveTokens:4000,promptTokens:5000,windowTokens:32768,active:false});
  try {
    render(<EchoContextStatus conversationId="game" running={false} />);
    expect(await screen.findByText('Last prompt snapshot')).toBeVisible();
    expect(screen.getByText('5,000 last request / 32,768 tokens · 15%')).toBeVisible();
    expect(read).toHaveBeenCalledWith('game');
  } finally { read.mockRestore(); }
});
