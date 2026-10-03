import { useCallback, useEffect, useState } from 'react';
import { Copy, Network, RefreshCw } from 'lucide-react';
import * as api from './api';

export function ClaudeBridgePanel({onNotice}:{onNotice:(text:string)=>void}) {
  const [status,setStatus]=useState<api.ClaudeBridgeStatus|null>(null);
  const [busy,setBusy]=useState(false), [error,setError]=useState('');
  const refresh=useCallback(async()=>{
    try {setStatus(await api.claudeBridgeStatus());setError('');} catch(cause) {setError(String(cause));}
  },[]);
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),15000);return()=>clearInterval(timer);},[refresh]);
  async function install() {
    setBusy(true);setError('');
    try {setStatus(await api.installClaudeBridge());onNotice('Claude bridge installed. Launch Claude Code with the command shown in Connectors.');}
    catch(cause){setError(String(cause));}finally{setBusy(false);}
  }
  async function copy() {
    try {await navigator.clipboard.writeText(status?.launchCommand || '');onNotice('Claude bridge launch command copied.');}
    catch(cause){setError(`Could not copy the launch command: ${String(cause)}`);}
  }
  const label=!status?'Checking bridge…':status.connected?(status.active?'Connected · working':'Connected'):status.installed?'Installed · awaiting connection':'Not installed';
  return <section className="claude-bridge-panel" aria-label="Claude Code bridge">
    <header><div><Network size={18}/><h2>Claude Code bridge</h2></div><strong role="status">{label}</strong></header>
    <p>Automatically recall project history from ECHO, record tool activity, and send generation jobs to Music Studio or Assets Studio.</p>
    <p>Requires Claude Code {status?.minimumVersion || '2.1.287'} or newer with Mods enabled. Install this plugin, then use the launch command in the project you want to connect.</p>
    <div className="claude-bridge-actions"><button className="primary" disabled={busy} onClick={()=>void install()}>{busy?'Installing bridge…':status?.installed?'Update Claude bridge':'Install Claude bridge'}</button><button onClick={()=>void refresh()}><RefreshCw size={14}/>Refresh connection</button></div>
    {status?.installed&&<div className="claude-bridge-command"><code>{status.launchCommand}</code><button onClick={()=>void copy()} aria-label="Copy Claude bridge launch command"><Copy size={14}/></button></div>}
    {status?.installed&&<p>In Claude Code, use <code>/opencore-bridge:music</code> or <code>/opencore-bridge:assets</code>. Generation starts after the Claude turn ends. The plugin reports the result when the job finishes.</p>}
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}
