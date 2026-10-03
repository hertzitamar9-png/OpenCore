import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import * as api from './api';
import { ClaudeBridgePanel } from './ClaudeBridgePanel';

const status: api.ClaudeBridgeStatus = {installed:false,connected:false,active:false,pluginPath:'C:/OpenCore/claude-bridge',launchCommand:'claude --plugin-dir "C:/OpenCore/claude-bridge"',lastSeen:null,minimumVersion:'2.1.287'};
describe('ClaudeBridgePanel',()=>{
  it('shows automatic setup without an install button or special launch command',async()=>{
    const read=vi.spyOn(api,'claudeBridgeStatus').mockResolvedValue({...status,installed:true});
    const install=vi.spyOn(api,'installClaudeBridge');
    try {
      render(<ClaudeBridgePanel onNotice={()=>{}}/>);
      expect(await screen.findByText('Ready · awaiting connection')).toBeVisible();
      expect(install).not.toHaveBeenCalled();
      expect(screen.queryByRole('button',{name:/install|update|copy/i})).not.toBeInTheDocument();
      expect(screen.queryByText(status.launchCommand)).not.toBeInTheDocument();
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
  it('reports automatic provisioning failure instead of claiming readiness',async()=>{
    const read=vi.spyOn(api,'claudeBridgeStatus').mockResolvedValue({...status,setupError:'Plugin blocked by managed policy'});
    try {
      render(<ClaudeBridgePanel onNotice={()=>{}}/>);
      expect(await screen.findByText('Connection unavailable')).toBeVisible();
      expect(screen.getByRole('alert')).toHaveTextContent('Plugin blocked by managed policy');
      expect(screen.queryByText('Ready · awaiting connection')).not.toBeInTheDocument();
    } finally {read.mockRestore();}
  });
});
