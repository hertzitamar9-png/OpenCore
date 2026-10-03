import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import * as api from './api';
import { ClaudeBridgePanel } from './ClaudeBridgePanel';

const status: api.ClaudeBridgeStatus = {installed:false,connected:false,active:false,pluginPath:'C:/OpenCore/claude-bridge',launchCommand:'claude --plugin-dir "C:/OpenCore/claude-bridge"',lastSeen:null,minimumVersion:'2.1.287'};
describe('ClaudeBridgePanel',()=>{
  it('distinguishes installed from connected and installs only on a click',async()=>{
    const read=vi.spyOn(api,'claudeBridgeStatus').mockResolvedValue(status);
    const install=vi.spyOn(api,'installClaudeBridge').mockResolvedValue({...status,installed:true});
    try {
      render(<ClaudeBridgePanel onNotice={()=>{}}/>);
      expect(await screen.findByText('Not installed')).toBeVisible();
      expect(install).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button',{name:'Install Claude bridge'}));
      expect(await screen.findByText('Installed · awaiting connection')).toBeVisible();
      expect(screen.getByText(status.launchCommand)).toBeVisible();
      expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    } finally {read.mockRestore();install.mockRestore();}
  });
  it('shows connection failure without claiming the bridge is installed',async()=>{
    const read=vi.spyOn(api,'claudeBridgeStatus').mockRejectedValue(Error('IPC unavailable'));
    try {
      render(<ClaudeBridgePanel onNotice={()=>{}}/>);
      expect(await screen.findByRole('alert')).toHaveTextContent('IPC unavailable');
      expect(screen.queryByText('Connected')).not.toBeInTheDocument();
    } finally {read.mockRestore();}
  });
});
