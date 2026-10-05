import { act,fireEvent,render,screen,waitFor } from '@testing-library/react';
import { beforeEach,expect,it,vi } from 'vitest';
import { AgentQuestions } from './AgentQuestions';
const bridge=vi.hoisted(()=>({handlers:new Map<string,(event:{payload:unknown})=>void>(),invoke:vi.fn()}));
vi.mock('@tauri-apps/api/core',()=>({invoke:bridge.invoke}));
vi.mock('@tauri-apps/api/event',()=>({listen:vi.fn(async(name:string,handler:(event:{payload:unknown})=>void)=>{bridge.handlers.set(name,handler);return()=>bridge.handlers.delete(name);})}));
vi.mock('@tauri-apps/plugin-opener',()=>({openUrl:vi.fn()}));
beforeEach(()=>{bridge.handlers.clear();bridge.invoke.mockReset().mockResolvedValue(undefined);});
it('answers the actual question in the requesting conversation',async()=>{
 render(<AgentQuestions/>);await waitFor(()=>expect(bridge.handlers.has('opencore-agent-question-request')).toBe(true));
 act(()=>bridge.handlers.get('opencore-agent-question-request')?.({payload:{requestId:'q1',conversationId:'older-chat',method:'item/tool/requestUserInput',params:{questions:[{id:'target',header:'Target',question:'Which target?',options:[{label:'Android',description:'Phone tests'},{label:'Desktop',description:'PC tests'}]}]}}}));
 fireEvent.change(screen.getByLabelText('Which target?'),{target:{value:'Android'}});fireEvent.click(screen.getByText('Submit'));
 await waitFor(()=>expect(bridge.invoke).toHaveBeenCalledWith('answer_agent_question',{requestId:'q1',conversationId:'older-chat',response:{answers:{target:{answers:['Android']}}}}));
});
it('handles structured MCP input and clears a cancelled request',async()=>{
 render(<AgentQuestions/>);await waitFor(()=>expect(bridge.handlers.size).toBe(2));
 act(()=>bridge.handlers.get('opencore-agent-question-request')?.({payload:{requestId:'q2',conversationId:'chat',method:'mcpServer/elicitation/request',params:{message:'Choose settings',requestedSchema:{type:'object',properties:{port:{type:'integer',title:'Port'}},required:['port']}}}}));
 fireEvent.change(screen.getByLabelText('Port'),{target:{value:'8812'}});fireEvent.click(screen.getByText('Submit'));
 await waitFor(()=>expect(bridge.invoke).toHaveBeenCalledWith('answer_agent_question',expect.objectContaining({response:{action:'accept',content:{port:8812}}})));
});
