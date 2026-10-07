import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RuntimeSetupControls } from './RuntimeSetupControls';
import * as setup from './runtimeSetupApi';

const recipe: setup.SetupRecipe = {id: 'image-v1', label: 'Image runtime', kind: 'studio', modelIds: ['sana-16'], packages: {}, minimumDiskBytes: 9e9, minimumRamBytes: 8e9, requiresCuda: true, sourceUrls: [], limitations: 'Original precision'};
const receipt: setup.SetupReceipt = {schema: 1, targetId: 'sana-16', recipeId: recipe.id, recipeFingerprint: 'pin', verifiedAt: '2026-10-07', dependenciesVerified: true, inferenceVerified: false, python: 'C:\\OpenCore\\runtime-setup\\python.exe', sourceUrls: []};
const snapshot: setup.SetupSnapshot = {recipes: [recipe], jobs: [], receipts: [], activeJobId: null, managedRoot: 'C:\\OpenCore\\runtime-setup'};
afterEach(() => vi.restoreAllMocks());

it('does not call dependency verification completed model inference', async () => {
  vi.spyOn(setup, 'runtimeSetupStatus').mockResolvedValue({...snapshot, receipts: [receipt]});
  render(<RuntimeSetupControls targetId="sana-16" label="Sana" installed />);
  expect(await screen.findByText('Dependencies verified · inference not verified')).toBeVisible();
  expect(screen.queryByText('Inference verified')).not.toBeInTheDocument();
});

it('keeps unsupported architectures explicit without an automatic installation button', async () => {
  vi.spyOn(setup, 'runtimeSetupStatus').mockResolvedValue(snapshot);
  render(<RuntimeSetupControls targetId="trellis-2-4b" label="TRELLIS" />);
  expect(await screen.findByText(/Automatic runtime setup is unavailable/)).toBeVisible();
  expect(screen.queryByRole('button', {name: /Set up automatically/})).not.toBeInTheDocument();
});

it('restores persisted running setup and lets the user cancel its real job', async () => {
  const job: setup.SetupJob = {id: 'persisted-job', targetId: 'sana-16', recipeId: recipe.id, status: 'running', stage: 'installing-dependencies', detail: 'Installing pinned packages', createdAt: '', updatedAt: '', downloadedBytes: 0, totalBytes: 0, diagnostics: [], error: null, receipt: null};
  vi.spyOn(setup, 'runtimeSetupStatus').mockResolvedValue({...snapshot, jobs: [job], activeJobId: job.id});
  const cancel = vi.spyOn(setup, 'runtimeSetupCancel').mockResolvedValue();
  render(<RuntimeSetupControls targetId="sana-16" label="Sana" />);
  expect(await screen.findByText('Installing pinned packages')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name: 'Cancel setup'}));
  await waitFor(() => expect(cancel).toHaveBeenCalledWith('persisted-job'));
});

it('requires Android license acceptance before issuing provisioning', async () => {
  vi.spyOn(setup, 'setupDesktopAvailable').mockReturnValue(true);
  const android = {...recipe, kind: 'android', modelIds: ['testing-android'], requiresLicenseAcceptance: true};
  vi.spyOn(setup, 'runtimeSetupStatus').mockResolvedValue({...snapshot, recipes: [android]});
  const start = vi.spyOn(setup, 'runtimeSetupStart').mockRejectedValue(new Error('fixture rejection'));
  render(<RuntimeSetupControls targetId="testing-android" label="Android emulator" />);
  const button = await screen.findByRole('button', {name: 'Set up automatically'});
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', {name: /I accept/}));
  fireEvent.click(button);
  await waitFor(() => expect(start).toHaveBeenCalledWith('testing-android', expect.objectContaining({acceptLicenses: true, installWeights: false})));
});
