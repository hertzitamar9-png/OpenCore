import{expect,it,vi,afterEach}from'vitest';
import{render,screen,waitFor,cleanup}from'@testing-library/react';
import{chatPostprocessing,ChatMemoryStatus}from'./chat-postprocessing.js';
const user={id:1,kind:'message',role:'user',timestamp:'2026-10-28T11:57:00Z',content:'I played professional ⚽ a year ago'};
const capture={id:2,title:'Automatic memory',timestamp:'2026-10-28T11:57:05Z',metadata:{status:'checking',userEntryId:1}};
afterEach(()=>{cleanup();delete(globalThis as any).__opencoreMemoryService;delete(window as any).__TAURI_INTERNALS__;});
it('separates post-response memory checking from text generation and ignores old captures',()=>{
 expect(chatPostprocessing([user,capture])).toBe(true);
 expect(chatPostprocessing([user,{...capture,metadata:{status:'failed'}}])).toBe(false);
 expect(chatPostprocessing([capture,{...user,timestamp:'2026-10-28T11:58:00Z'}])).toBe(false);
});
it('recovers failed capture using exact-source proof, revalidates, and requires confirmed native persistence',async()=>{
 const args={action:'record',content:user.content,source:'chat:chat:user-entry:1'},refresh=vi.fn(),native=vi.fn(async()=>({persisted:true}));
 let checks=0;
 const service=vi.fn(async(action:string,_body:any)=>action==='recover'?{status:'ready',candidates:[{recordArgs:args}]}:++checks===1?{status:'ready',candidates:[{recordArgs:args}]}:{status:'already_recorded',candidates:[]});
 (globalThis as any).__opencoreMemoryService=service;(window as any).__TAURI_INTERNALS__={invoke:native};
 render(<ChatMemoryStatus conversationId="chat" entries={[user,{...capture,metadata:{status:'failed',userEntryId:1}}]} runtime={{status:'running',backendPort:8811,echoPort:8813}} active={false} onRefresh={refresh}/>);
 await waitFor(()=>expect(refresh).toHaveBeenCalledOnce());
 expect(native).toHaveBeenCalledWith('agent_platform_action',{name:'agent_memory',args});
 expect(service.mock.calls[0][1]).toMatchObject({conversationId:'chat',userEntryId:1,temporaryChat:false});
 expect(screen.queryByText(/Retrying memory/)).toBeNull();
});
