"""Explicit, local prompt rewriting. No conversations, memories or cloud writes."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from hf_memory_repair import MemoryRepair,RepairError

SCHEMA={'type':'object','additionalProperties':False,'required':['prompt','recap'],'properties':{
    'prompt':{'type':'string','minLength':1},'recap':{'type':'string','minLength':1,'maxLength':300}}}

class PromptAssistant(MemoryRepair):
    def improve(self,request):
        diagnostic={'cacheRestored':False,'leaseReleased':False};lease=None;touched=False;cold=False;failure=None;result=None
        if not self._gate.acquire(blocking=False):return {'status':'error','message':'Another prompt rewrite is running.'}
        try:
            if not isinstance(request,dict) or request.get('enabled') is not True:raise RepairError('disabled','Enable the optional prompt assistant in Settings first.')
            prompt=request.get('prompt');mode=request.get('mode','image')
            if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000 or mode not in ('image','chat'):raise RepairError('invalid_prompt','Enter a prompt of up to 8000 characters.')
            backend,echo,authorization=self._runtime(request)
            before=self._slot(backend,authorization)
            if before.get('is_processing'):raise RepairError('backend_busy','Wait for the active model request to finish.')
            cold=self._resettable_slot(before)
            conversation=request.get('conversationId')
            digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
            if not cold and (not isinstance(conversation,str) or digest(conversation)!=before.get('echo_session_id')):
                with closing(sqlite3.connect('file:'+str(self.data/'control-center.sqlite3').replace('\\','/')+'?mode=ro',uri=True)) as database:
                    conversation=next((row[0] for row in database.execute('SELECT DISTINCT conversation_id FROM timeline LIMIT 20000') if digest(row[0])==before.get('echo_session_id')),None)
            if not cold and (not conversation or before.get('echo_memory_branch_supported') is not True):raise RepairError('cache_owner','The current model cache cannot be preserved. Restart the selected prompt model and retry.')
            conversation=conversation or 'prompt-assistant:'+uuid.uuid4().hex
            acquired=self._http(echo+'/echo/kv/lease',{'action':'acquire','conversation_id':conversation})
            lease=acquired.get('nonce') if acquired.get('acquired') is True else None
            if not lease:raise RepairError('backend_busy','The text model is busy. Retry when it finishes.')
            branch=uuid.uuid4().hex
            body={'model':'opencore','messages':[{'role':'system','content':
                'Improve the supplied '+('image generation' if mode=='image' else 'chat')+' prompt. Preserve the user intent, subjects, constraints and reference-image instructions. Do not answer or perform the request. Do not add claims about the user or change requested content. Add only useful clarity, composition or precise wording. Return JSON with prompt and a short recap of what the prompt requests. The recap is a request summary, never a claim that an image was seen or generated. No tools, reasoning or markdown.'},
                {'role':'user','content':json.dumps({'originalPrompt':prompt},ensure_ascii=False)}],
                'stream':False,'temperature':0.2,'max_tokens':1600,'reasoning_budget_tokens':0,'reasoning_effort':'none','chat_template_kwargs':{'enable_thinking':False},'tools':[],'tool_choice':'none',
                'response_format':{'type':'json_object','schema':SCHEMA},'id_slot':0,'echo_append':True,'echo_cached_tool_header':False,'echo_memory_branch':branch,'echo_session_id':before.get('echo_session_id'),'cache_prompt':True}
            if cold:
                body.pop('echo_memory_branch');body.update(echo_append=False,echo_reset=True,cache_prompt=False,echo_session_id=digest('prompt-assistant:'+branch))
            touched=True;response=self._http(backend+'/v1/chat/completions',body,authorization,timeout=60)
            if cold:
                if not self._check_standalone_finished(backend,authorization,body['echo_session_id']):raise RepairError('cache_reset','Prompt model did not finish its temporary session.')
            else:
                diagnostic['cacheRestored']=self._check_restored(backend,authorization,before,branch)
                if not diagnostic['cacheRestored']:raise RepairError('cache_restore','The model did not confirm restoring its chat cache.')
            choice=response['choices'][0]
            if choice.get('finish_reason')!='stop' or choice['message'].get('tool_calls'):raise RepairError('incomplete','The prompt rewrite did not finish. Your original prompt is kept.')
            result=json.loads(choice['message'].get('content') or '')
            if not isinstance(result,dict) or set(result)!={'prompt','recap'} or not all(isinstance(result[key],str) and result[key].strip() and len(result[key])<=limit for key,limit in [('prompt',8000),('recap',300)]):raise RepairError('invalid_response','The model returned an invalid prompt rewrite. Your original prompt is kept.')
        except (RepairError,ValueError,KeyError,TypeError,sqlite3.Error) as error:failure=str(error)
        finally:
            if lease:
                try:
                    released=self._http(echo+'/echo/kv/lease',{'action':'release','nonce':lease,'invalidate':touched and (cold or not diagnostic['cacheRestored'])})
                    diagnostic['leaseReleased']=released.get('released') is True
                    if cold and touched:
                        invalidation=released.get('invalidation') or {}
                        if invalidation.get('invalidated') is True and invalidation.get('nextTurn')=='full-transcript-reset':self._remember_discarded_session(body['echo_session_id'])
                        else:failure='The prompt model did not confirm resetting its temporary context.'
                except RepairError:failure='Could not release the temporary prompt-model session.'
                if not diagnostic['leaseReleased']:failure='The prompt-model lease release was not confirmed.'
            self._gate.release()
        return {'status':'error','message':failure,'diagnostics':diagnostic} if failure else {'status':'ready',**result,'diagnostics':diagnostic}
