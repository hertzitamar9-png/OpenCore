"""Group isolation, task ownership, replay safety and real pinned-TLS transport."""
import concurrent.futures
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from group_chats import GroupStore, GroupManager, parse_invite, peer_request
from remote_access import RemoteServer
import test_bridge


class Groups(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=10000
        self.store=GroupStore(self.root,lambda:self.now);self.addCleanup(self.store.close)
        self.owner=self.store.create('Friends','Owner','pc-owner','Owner PC')
        self.friend=self.join('Friend','pc-friend')

    def join(self,name,pc):
        invite=self.store.invite(self.owner['groupId'],self.owner['token'])
        return self.store.join(self.owner['groupId'],invite['code'],name,pc,pc)

    def rpc(self,member,action,**body):
        return self.store.request(member['groupId'],member['token'],action,body)

    def share(self,member,model='model-a'):
        return self.rpc(member,'agents',models=[{'id':model,'name':member['name']+' agent','label':model}])['agents'][-1]

    def queue(self,agents,request='request-one'):
        return self.rpc(self.owner,'message',text='Build the shared task',requestId=request,agentIds=[a['id'] for a in agents])

    def update(self,member,job,**body):
        return self.rpc(member,'update',taskId=job['id'],leaseToken=job['leaseToken'],**body)

    def tool(self,member,job,tool,**args):
        return self.rpc(member,'agent-tool',taskId=job['id'],leaseToken=job['leaseToken'],tool=tool,args=args)

    def test_invitation_is_one_use_expiring_and_membership_scoped(self):
        invite=self.store.invite(self.owner['groupId'],self.owner['token'])
        self.store.join(self.owner['groupId'],invite['code'],'New','pc-new','New PC')
        with self.assertRaises(PermissionError):self.store.join(self.owner['groupId'],invite['code'],'Again','pc-again','Again PC')
        expired=self.store.invite(self.owner['groupId'],self.owner['token']);self.now+=901
        with self.assertRaises(PermissionError):self.store.join(self.owner['groupId'],expired['code'],'Late','pc-late','Late PC')
        other=self.store.create('Other','Other','pc-other','Other PC')
        with self.assertRaises(PermissionError):self.store.request(other['groupId'],self.friend['token'],'view',{})
        with self.assertRaises(PermissionError):self.store.invite(self.friend['groupId'],self.friend['token'])

    def test_plain_chat_does_not_run_agents_and_mentions_are_idempotent(self):
        a=self.share(self.owner)
        self.rpc(self.owner,'message',text='Hello friends',requestId='hello',agentIds=[])
        self.assertIsNone(self.rpc(self.owner,'claim')['task'])
        self.queue([a]);self.queue([a])
        view=self.rpc(self.friend,'view');self.assertEqual(len(view['tasks']),1)
        self.assertEqual(len(view['messages']),2)
        with self.assertRaises(ValueError):self.rpc(self.owner,'message',text='Changed body',requestId='request-one',agentIds=[])

    def test_separate_pcs_claim_in_parallel_and_cannot_claim_each_others_tasks(self):
        a=self.share(self.owner);b=self.share(self.friend,'model-b')
        self.queue([a,b])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            owner,friendly=list(pool.map(lambda member:self.rpc(member,'claim')['task'],[self.owner,self.friend]))
        self.assertEqual((owner['modelId'],friendly['modelId']),('model-a','model-b'))
        self.assertIsNone(self.rpc(self.owner,'claim')['task'])
        with self.assertRaises(PermissionError):self.update(self.friend,owner,status='completed',text='Stolen')
        with self.assertRaises(PermissionError):self.rpc(self.owner,'update',taskId=owner['id'],leaseToken='wrong',status='completed',text='No')
        self.update(self.owner,owner,status='completed',text='Owner result')
        self.update(self.friend,friendly,status='completed',text='Friend result')
        view=self.rpc(self.owner,'view')
        self.assertEqual({m['text'] for m in view['messages'] if m['kind']=='agent'},{'Owner result','Friend result'})
        self.assertNotIn('leaseHash',json.dumps(view));self.assertNotIn(owner['leaseToken'],json.dumps(view))
        self.update(self.owner,owner,status='completed',text='Owner result')
        self.assertEqual(len(self.rpc(self.owner,'view')['messages']),3)

    def test_task_plan_keeps_real_step_status_and_result_does_not_trigger_other_agents(self):
        a=self.share(self.owner);self.share(self.friend,'model-b');self.queue([a]);job=self.rpc(self.owner,'claim')['task']
        self.tool(self.owner,job,'plan',steps=[{'text':'Inspect','status':'completed'},{'text':'Build','status':'in_progress'}])
        self.tool(self.owner,job,'message',text='Inspection finished')
        self.update(self.owner,job,status='completed',text='Here is the result')
        view=self.rpc(self.friend,'view');self.assertEqual([step['status'] for step in view['plan']],['completed','in_progress'])
        self.assertIsNone(self.rpc(self.friend,'claim')['task'])
        self.assertEqual(len(view['tasks']),1)

    def test_delegation_has_real_tasks_and_blocks_cycles(self):
        a=self.share(self.owner);b=self.share(self.friend,'model-b');self.queue([a]);first=self.rpc(self.owner,'claim')['task']
        result=self.tool(self.owner,first,'delegate',agentId=b['id'],text='Review this task')
        self.assertEqual(result['task']['status'],'queued')
        self.assertEqual(self.tool(self.owner,first,'delegate',agentId=b['id'],text='Review this task')['taskId'],result['taskId'])
        second=self.rpc(self.friend,'claim')['task'];self.assertEqual(second['parentId'],first['id'])
        with self.assertRaisesRegex(ValueError,'own task chain'):self.tool(self.friend,second,'delegate',agentId=a['id'],text='Back to you')
        for n in range(14):self.tool(self.owner,first,'delegate',agentId=b['id'],text='Independent bounded task '+str(n))
        with self.assertRaisesRegex(ValueError,'budget'):self.tool(self.owner,first,'delegate',agentId=b['id'],text='Over budget')

    def test_disconnected_tasks_fail_once_and_never_requeue_pc_actions(self):
        a=self.share(self.owner);self.queue([a]);job=self.rpc(self.owner,'claim')['task'];self.now+=121
        view=self.rpc(self.owner,'view');self.assertEqual(view['tasks'][0]['status'],'failed')
        self.assertIsNone(self.rpc(self.owner,'claim')['task'])
        with self.assertRaises(PermissionError):self.update(self.owner,job,status='completed',text='Too late')

    def test_unsharing_and_revocation_cancel_pending_tasks(self):
        a=self.share(self.friend,'model-b');self.queue([a]);self.rpc(self.friend,'agents',models=[])
        self.assertEqual(self.rpc(self.owner,'view')['tasks'][0]['status'],'cancelled')
        a=self.share(self.friend,'model-b');self.queue([a],request='second');job=self.rpc(self.friend,'claim')['task']
        self.rpc(self.owner,'revoke',memberId=self.friend['memberId'])
        with self.assertRaises(PermissionError):self.rpc(self.friend,'view')
        self.assertEqual({job['status'] for job in self.rpc(self.owner,'view')['tasks']},{'cancelled'})

    def test_invalid_model_choices_and_steps_are_rejected_without_mutation(self):
        with self.assertRaises(ValueError):self.rpc(self.owner,'agents',models=[None])
        a=self.share(self.owner);self.queue([a]);job=self.rpc(self.owner,'claim')['task']
        with self.assertRaises(ValueError):self.tool(self.owner,job,'plan',steps=[None])
        self.assertEqual(self.rpc(self.owner,'view')['plan'],[])


class Remote:
    pc_name='Test PC'
    server=None
    def status(self):return {'enabled':False}
    def authorize(self,authorization):return None


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.manager=GroupManager(self.root,Remote(),context_root=self.root/'contexts');self.addCleanup(self.manager.close)
        self.manager.base_url='http://127.0.0.1:12345'
        self.group=self.manager.local('create',{'name':'Group','displayName':'Owner'})['id']

    def test_new_membership_cannot_execute_models_without_local_opt_in(self):
        self.assertFalse(self.manager.local('view',{'id':self.group})['sharing']['enabled'])
        self.assertEqual(self.manager.local('worker/poll',{})['jobs'],[])
        self.assertEqual(self.manager.local('view',{'id':self.group})['agents'],[])

    def start_job(self):
        view=self.manager.local('sharing',{'id':self.group,'enabled':True,'models':[{'id':'model-a','label':'Model A'}],'approvalMode':'ask-every-time'})
        self.manager.local('message',{'id':self.group,'text':'Shared task','requestId':'request','agentIds':[view['agents'][0]['id']]})
        return self.manager.local('worker/poll',{})['jobs'][0]

    def test_runner_capability_only_owns_dedicated_conversation_and_is_retired(self):
        item=self.start_job();task=item['task'];lease={key:item[key] for key in ('connectionId',)};lease.update(taskId=task['id'],leaseToken=task['leaseToken'])
        with self.assertRaises(PermissionError):self.manager.local('worker/register',{**lease,'conversationId':'private-chat'})
        self.manager.local('worker/register',{**lease,'conversationId':task['conversationId']})
        context=self.root/'contexts'/str(task['conversationId']+'.json');value=json.loads(context.read_text())
        self.assertEqual(value['url'],'http://127.0.0.1:12345/api/groups/agent/tool')
        self.assertNotIn('token',json.dumps(self.manager.local('view',{'id':self.group})))
        self.manager.agent_tool('Bearer '+value['token'],{'action':'plan','steps':[{'text':'Work','status':'in_progress'}]})
        with self.assertRaises(PermissionError):self.manager.agent_tool('Bearer wrong',{'action':'message','text':'No'})
        with self.assertRaises(ValueError):self.manager.agent_tool('Bearer '+value['token'],{'action':'send_chat_message'})
        self.manager.local('worker/update',{**lease,'status':'completed','text':'Result'})
        self.assertFalse(context.exists())
        with self.assertRaises(PermissionError):self.manager.agent_tool('Bearer '+value['token'],{'action':'context'})

    def test_one_active_local_worker_and_busy_check_preserve_foreground(self):
        item=self.start_job();self.assertEqual(self.manager.local('worker/poll',{})['jobs'],[])
        self.assertEqual(self.manager.local('worker/poll',{'busy':True})['jobs'],[])
        self.assertEqual(item['approvalMode'],'ask-every-time')

    def test_worker_status_uses_its_authenticated_lease_outside_the_display_window(self):
        item=self.start_job();task=item['task'];lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        view=self.manager.local('view',{'id':self.group});agent=view['agents'][0]['id']
        for index in range(50):
            self.manager.local('message',{'id':self.group,'text':'Later '+str(index),'requestId':'later-'+str(index),'agentIds':[agent]})
        self.assertNotIn(task['id'],[job['id'] for job in self.manager.local('view',{'id':self.group})['tasks']])
        self.assertEqual(self.manager.local('worker/status',lease)['status'],'running')

    def test_owner_only_agent_configuration_and_project_bindings_are_never_advertised(self):
        project=self.manager.local('project-save',{'id':self.group,'name':'Website'})['project']
        shared=self.manager.local('sharing',{'id':self.group,'enabled':True,'models':[{
            'id':'api:vendor','key':'reviewer','label':'API Reviewer','name':'Reviewer','instructions':'LOCAL INSTRUCTIONS',
            'projectBindings':{project['id']:'local-project'},'apiKey':'NEVER SEND'}],
            'projectBindings':{project['id']:'default-local-project'}})
        connection=self.manager._connection(self.group)
        public=self.manager.store.request(self.group,connection['token'],'view',{})
        self.assertNotIn('LOCAL INSTRUCTIONS',json.dumps(public))
        self.assertNotIn('local-project',json.dumps(public))
        self.assertNotIn('NEVER SEND',self.manager.path.read_text())
        channel=self.manager.local('channel-save',{'id':self.group,'name':'Project','projectId':project['id']})['channel']
        self.manager.local('message',{'id':self.group,'channelId':channel['id'],'requestId':'local-binding','text':'Review',
                                     'agentIds':[shared['agents'][0]['id']]})
        job=self.manager.local('worker/poll',{})['jobs'][0]
        self.assertEqual(job['localProjectId'],'local-project')
        self.assertEqual(job['instructions'],'LOCAL INSTRUCTIONS')

    def test_new_native_conversation_is_bound_locally_instead_of_trusting_host_chat_id(self):
        item=self.start_job();task=item['task']
        lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        fresh='a'*32
        self.manager.local('worker/register',{**lease,'conversationId':fresh,'createdConversation':True})
        self.assertTrue((self.root/'contexts'/(fresh+'.json')).is_file())
        self.assertFalse((self.root/'contexts'/(task['conversationId']+'.json')).exists())
        with self.assertRaises(PermissionError):
            self.manager.local('worker/register',{**lease,'conversationId':'b'*32,'createdConversation':True})

    def test_advanced_execution_controls_are_saved_locally_and_validated(self):
        body={'id':self.group,'enabled':True,'models':[{'id':'model-a','key':'builder','name':'Builder',
            'prepareIfMissing':True,'catalogId':'model-a','reasoningEffort':'high','approvalMode':'ask-every-time',
            'subagentsEnabled':False,'maxSubagents':2,'skills':['browser-use'],'historyMessages':4,
            'priority':8,'allowDelegation':False,'acceptDelegation':False}]}
        result=self.manager.local('sharing',body)
        saved=result['sharing']['models'][0]
        self.assertEqual(saved['reasoningEffort'],'high');self.assertEqual(saved['skills'],['browser-use'])
        self.assertFalse(saved['subagentsEnabled']);self.assertTrue(saved['prepareIfMissing'])
        public=result['agents'][0]
        self.assertNotIn('reasoningEffort',public);self.assertNotIn('skills',public)
        self.assertEqual(public['historyMessages'],4);self.assertEqual(public['priority'],8)
        for key,value in [('historyMessages',101),('reasoningEffort','not-real'),('skills',['untrusted-command']),('priority',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.manager.local('sharing',{**body,'models':[{**body['models'][0],key:value}]})

    def test_disabling_sharing_revokes_running_task_capability_immediately(self):
        item=self.start_job();task=item['task'];lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        self.manager.local('worker/register',{**lease,'conversationId':task['conversationId']})
        context=self.root/'contexts'/(task['conversationId']+'.json');token=json.loads(context.read_text())['token']
        view=self.manager.local('sharing',{'id':self.group,'enabled':False,'models':[]})
        self.assertFalse(context.exists());self.assertEqual(view['tasks'][0]['status'],'cancelled')
        with self.assertRaises(PermissionError):self.manager.agent_tool('Bearer '+token,{'action':'message','text':'Stopped'})
        with self.assertRaises(PermissionError):self.manager.local('worker/update',{**lease,'status':'running'})

    def test_capability_expiry_is_enforced_by_server_even_with_live_worker_lease(self):
        item=self.start_job();task=item['task'];lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        self.manager.local('worker/register',{**lease,'conversationId':task['conversationId']})
        active=self.manager.active[task['id']];token=active['runnerToken'];active['runnerExpires']=0
        with self.assertRaisesRegex(PermissionError,'expired'):self.manager.agent_tool('Bearer '+token,{'action':'context'})
        self.assertFalse(Path(active['contextFile']).exists())

    def test_offline_opt_out_retires_context_and_persists_before_host_ack(self):
        item=self.start_job();task=item['task'];lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        self.manager.local('worker/register',{**lease,'conversationId':task['conversationId']})
        context=self.root/'contexts'/(task['conversationId']+'.json');token=json.loads(context.read_text())['token']
        rpc=self.manager._rpc
        def offline(*args,**kwargs):raise OSError('Host offline')
        self.manager._rpc=offline
        result=self.manager.local('sharing',{'id':self.group,'enabled':False,'models':[]})
        self.assertTrue(result['localOnly']);self.assertFalse(context.exists());self.assertEqual(self.manager.active,{})
        self.assertFalse(json.loads(self.manager.path.read_text())['connections'][self.group]['sharing']['enabled'])
        with self.assertRaises(PermissionError):self.manager.agent_tool('Bearer '+token,{'action':'context'})
        # Re-enabling even the same model uses a new consent generation, so old
        # queued actions cannot execute after an offline revocation.
        self.manager._rpc=rpc
        self.manager.local('sharing',{'id':self.group,'enabled':True,'models':[{'id':'model-a','label':'Model A'}]})
        self.assertEqual(self.manager.local('worker/poll',{})['jobs'],[])
        self.assertEqual(self.manager.local('view',{'id':self.group})['tasks'][0]['status'],'cancelled')

    def test_offline_access_narrowing_revokes_active_local_capability(self):
        for field,value in [('allowedMembers',['another-member']),('allowedChannels',['another-channel'])]:
            with self.subTest(field=field):
                self.group=self.manager.local('create',{'name':'Offline scope','displayName':'Owner'})['id']
                item=self.start_job();task=item['task'];lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
                self.manager.local('worker/register',{**lease,'conversationId':task['conversationId']})
                context=self.root/'contexts'/(task['conversationId']+'.json')
                rpc=self.manager._rpc
                def offline(*args,**kwargs):raise OSError('Host offline')
                self.manager._rpc=offline
                try:
                    result=self.manager.local('sharing',{'id':self.group,'enabled':True,'models':[{'id':'model-a','label':'Model A',field:value}]})
                finally:
                    self.manager._rpc=rpc
                self.assertTrue(result['localOnly']);self.assertFalse(context.exists());self.assertFalse(self.manager.active)
                self.manager.local('sharing',{'id':self.group,'enabled':False,'models':[]})

    def test_invite_parser_rejects_missing_duplicate_or_unsafe_fields(self):
        good={'url':'https://127.0.0.1:12345','pin':'ab'*32,'group':'group','code':'x'*32}
        link=lambda data:'opencore://group?'+urlencode(data)
        self.assertEqual(parse_invite(link(good))['group'],'group')
        for value in [link({}),link({**good,'url':'http://127.0.0.1'}),link({**good,'url':'https://evil/path'}),link({**good,'pin':'bad'}),link(good)+'&code=duplicate']:
            with self.assertRaises(ValueError):parse_invite(value)


@unittest.skipUnless(os.name=='nt','Windows local TLS identity')
class PinnedTransport(unittest.TestCase):
    def test_two_pc_clients_exchange_tasks_over_real_tls_without_phone_or_native_access(self):
        from tls_identity import create_identity
        with ExitStack() as cleanup:
            directory=cleanup.enter_context(tempfile.TemporaryDirectory())
            root=Path(directory);cert,key,pin=create_identity();(root/'cert.pem').write_bytes(cert);(root/'key.pem').write_bytes(key)
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(root/'cert.pem',root/'key.pem')
            remote=Remote();host=GroupManager(root/'host',remote,context_root=root/'contexts')
            cleanup.callback(host.close)
            server=RemoteServer(('127.0.0.1',0),remote,tls);cleanup.callback(server.server_close);cleanup.callback(server.shutdown)
            threading.Thread(target=server.serve_forever,daemon=True).start();url='https://127.0.0.1:'+str(server.server_port)
            owner=host.local('create',{'name':'Friends','displayName':'Owner'});connection=host._connection(owner['id'])
            invite=host.store.invite(owner['id'],connection['token'])
            guest=GroupManager(root/'guest',Remote(),context_root=root/'guest-contexts');cleanup.callback(guest.close)
            joined=guest.local('join',{'invite':'opencore://group?'+urlencode({'url':url,'pin':pin,'group':owner['id'],'code':invite['code']}),'displayName':'Guest'})
            shared=guest.local('sharing',{'id':joined['id'],'enabled':True,'models':[{'id':'local-guest-model','label':'Guest model'}]})
            host.local('message',{'id':owner['id'],'requestId':'network-message','text':'Run on guest PC','agentIds':[shared['agents'][0]['id']]})
            job=guest.local('worker/poll',{})['jobs'][0]
            self.assertEqual(job['task']['modelId'],'local-guest-model')
            guest.local('worker/update',{'connectionId':job['connectionId'],'taskId':job['task']['id'],'leaseToken':job['task']['leaseToken'],'status':'completed','text':'Guest completed over TLS'})
            self.assertEqual(host.local('view',{'id':owner['id']})['messages'][-1]['text'],'Guest completed over TLS')
            with self.assertRaises(PermissionError):peer_request(url,'00'*32,'/groups/request',{},'private-token')
            token=guest._connection(joined['id'])['token']
            for route in ('/request','/ui','/media','/session'):
                with self.assertRaisesRegex(PermissionError,'Pair this phone'):peer_request(url,pin,route,{},token)


class GroupBridgeTests(unittest.TestCase):
    setUp=test_bridge.BridgeTests.setUp
    request=test_bridge.BridgeTests.request
    def test_group_routes_require_desktop_secret_and_do_not_listen_by_default(self):
        self.assertEqual(self.request('POST','/api/groups/list',{},secret=None)[0],401)
        self.assertIsNone(self.state.groups)
        value=self.request('POST','/api/groups/create',{'name':'Friends','displayName':'Owner'})
        self.assertEqual(value[0],200);self.assertIsNotNone(self.state.groups)
        self.assertIsNone(self.state.remote.server)
        self.assertEqual(self.request('POST','/api/groups/agent/tool',{'action':'context'},secret=None)[0],403)
        self.assertEqual(self.request('POST','/api/groups/agent/tool',{'action':'context'},origin='http://tauri.localhost')[0],403)

    def test_task_capability_uses_real_loopback_route_but_cannot_read_local_group_settings(self):
        group=self.request('POST','/api/groups/create',{'name':'Friends','displayName':'Owner'})[2]['id']
        self.state.groups.context_root=self.root/'contexts'
        shared=self.request('POST','/api/groups/sharing',{'id':group,'enabled':True,'models':[{'id':'model-a','label':'Model A'}]})[2]
        self.request('POST','/api/groups/message',{'id':group,'text':'Run this shared task','requestId':'actual-http-task','agentIds':[shared['agents'][0]['id']]})
        item=self.request('POST','/api/groups/worker/poll',{})[2]['jobs'][0];task=item['task']
        lease={'connectionId':item['connectionId'],'taskId':task['id'],'leaseToken':task['leaseToken']}
        self.assertEqual(self.request('POST','/api/groups/worker/register',{**lease,'conversationId':task['conversationId']})[0],200)
        context=json.loads((self.root/'contexts'/(task['conversationId']+'.json')).read_text())
        headers={'Authorization':'Bearer '+context['token']}
        code,_,value=self.request('POST','/api/groups/agent/tool',{'action':'plan','steps':[{'text':'Real progress','status':'in_progress'}]},secret=None,headers=headers)
        self.assertEqual(code,200);self.assertTrue(value['saved'])
        self.assertEqual(self.request('POST','/api/groups/list',{},secret=None,headers=headers)[0],401)
        self.assertEqual(self.request('POST','/api/groups/agent/tool',{'action':'context'},secret=None,headers=headers,origin='http://tauri.localhost')[0],403)
        self.request('POST','/api/groups/sharing',{'id':group,'enabled':False,'models':[]})
        self.assertEqual(self.request('POST','/api/groups/agent/tool',{'action':'context'},secret=None,headers=headers)[0],403)


if __name__=='__main__':unittest.main()
