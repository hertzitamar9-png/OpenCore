import { useCallback, useEffect, useState } from 'react';
import { Network, RefreshCw } from 'lucide-react';
import * as api from './api';

export function ClaudeBridgePanel(_props:{onNotice:(text:string)=>void}) {
  const [status,setStatus]=useState<api.ClaudeBridgeStatus|null>(null);
  const [error,setError]=useState('');
  const refresh=useCallback(async()=>{
    try {setStatus(await api.claudeBridgeStatus());setError('');} catch(cause) {setError(String(cause));}
  },[]);
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),15000);return()=>clearInterval(timer);},[refresh]);
  const label=!status?'Checking bridge…':status.connected?(status.active?'Connected · working':'Connected'):status.setupError?'Connection unavailable':status.enabled===false&&status.installed?'Disabled in Claude Code':status.installed?'Ready · awaiting connection':'Setting up automatically…';
  return <section className="claude-bridge-panel" aria-label="Claude Code bridge">
    <header><div><Network size={18}/><h2>Claude Code bridge</h2></div><strong role="status">{label}</strong></header>
    <p>Automatically recall project history from ECHO, record tool activity, and send generation jobs to Music Studio or Game Dev Studio.</p>
    <p>Included with OpenCore and configured automatically. Open Claude Code normally in your project to connect. Requires Claude Code {status?.minimumVersion || '2.1.287'} or newer with Mods available.</p>
    <div className="claude-bridge-actions"><button onClick={()=>void refresh()}><RefreshCw size={14}/>Refresh connection</button></div>
    {status?.installed&&<p>In Claude Code, use <code>/opencore-bridge:music</code> or <code>/opencore-bridge:assets</code>. Generation starts after the Claude turn ends. The plugin reports the result when the job finishes.</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {status?.setupError&&<p className="error" role="alert">{status.setupError}</p>}
  </section>;
}
