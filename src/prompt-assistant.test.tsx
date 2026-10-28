import{cleanup,fireEvent,render,screen,waitFor}from'@testing-library/react';
import{afterEach,beforeEach,expect,it,vi}from'vitest';
import{PromptAssistantTools,promptAssistantSettings}from'./prompt-assistant.js';
const storageDescriptor=Object.getOwnPropertyDescriptor(window,'localStorage');
beforeEach(()=>{const values=new Map<string,string>();Object.defineProperty(window,'localStorage',{configurable:true,value:{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)}});});
afterEach(()=>{cleanup();if(storageDescriptor)Object.defineProperty(window,'localStorage',storageDescriptor);delete(window as any).__TAURI_INTERNALS__;delete(globalThis as any).__opencorePromptService;});
it('stays off until enabled and never rewrites or starts a model by default',()=>{window.localStorage.removeItem('opencore.prompt-assistant.v1');expect(promptAssistantSettings().enabled).toBe(false);render(<PromptAssistantTools prompt="A cup" onPrompt={vi.fn()}/>);expect(screen.queryByRole('button',{name:'Improve prompt'})).toBeNull();});
it('shows the local rewrite for review and can restore the original draft',async()=>{
 window.localStorage.setItem('opencore.prompt-assistant.v1',JSON.stringify({enabled:true,profile:'small-model'}));
 (window as any).__TAURI_INTERNALS__={invoke:vi.fn(async()=>({runtime:{status:'running',profile:'small-model',backendPort:8811,echoPort:8813},activeConversationIds:[]}))};
 const service=vi.fn(async(_body:any)=>({status:'ready',prompt:'A sharp blue cup on a table',recap:'A cup on a table'}));(globalThis as any).__opencorePromptService=service;
 const change=vi.fn();render(<PromptAssistantTools prompt="A cup" onPrompt={change} conversationId="same-chat"/>);
 fireEvent.click(screen.getByRole('button',{name:'Improve prompt'}));await waitFor(()=>expect(change).toHaveBeenCalledWith('A sharp blue cup on a table'));
 expect(service.mock.calls[0][0]).toMatchObject({enabled:true,prompt:'A cup',mode:'chat',conversationId:'same-chat'});
 fireEvent.click(screen.getByRole('button',{name:'Undo rewrite'}));expect(change).toHaveBeenLastCalledWith('A cup');
});
