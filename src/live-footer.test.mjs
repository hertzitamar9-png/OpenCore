import React from 'react';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {it,expect,afterEach} from 'vitest';
import {render,screen,cleanup,waitFor} from '@testing-library/react';

afterEach(cleanup);
it('stopped streaming output remains ordinary reasoning and text without a second output panel',async()=>{
 const context={React,window,__opencoreLiveMetrics:{conversationId:'chat',active:true,
   preview:{id:'reply-1',phase:'working',reasoning:'Inspect the current files.',text:'I found the game.'}}};
 const Activity=runInNewContext(readFileSync('src/live-footer.js','utf8')+';LiveModelActivity',context);
 const entries=[{id:1,role:'user',kind:'message',content:'Find the game.'}];
 const view=render(React.createElement(Activity,{conversationId:"chat",active:true,entries}));
 await screen.findByText('Inspect the current files.',{exact:true});
 view.rerender(React.createElement(Activity,{conversationId:"chat",active:false,entries}));
 expect(screen.queryByText('Uncommitted model output',{exact:true})).toBeNull();
 expect(screen.getByText('I found the game.',{exact:true})).toBeVisible();
 expect(screen.getByText('Reasoned',{exact:true}).closest('summary').querySelector('svg.lucide-brain-circuit')).not.toBeNull();
 await waitFor(()=>expect(screen.getByText('Reasoned',{exact:true}).closest('details')).not.toHaveAttribute('open'));
 expect(screen.queryByRole('status')).toBeNull();
});

it('committed reasoning segments replace their matching live preview without a second disclosure',async()=>{
 const context={React,window,__opencoreLiveMetrics:{conversationId:'chat',active:true,
  preview:{id:'reply',phase:'working',reasoning:'I found Chrome.\n\nNow search for cats.',text:''}}};
 const Activity=runInNewContext(readFileSync('src/live-footer.js','utf8')+';LiveModelActivity',context);
 const user={id:1,role:'user',kind:'message',content:'Search for cats.'};
 const view=render(React.createElement(Activity,{conversationId:'chat',active:true,entries:[user]}));
 await screen.findByText('I found Chrome. Now search for cats.',{exact:false});
 view.rerender(React.createElement(Activity,{conversationId:'chat',active:false,entries:[user,
  {id:2,role:'assistant',kind:'thinking',content:'I found Chrome.'},
  {id:3,role:'assistant',kind:'thinking',content:'Now search for cats.'}]}));
 expect(view.container.querySelector('.live-model-preview')).toBeNull();
});
