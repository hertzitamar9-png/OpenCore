"""Shared channel privacy, deterministic agent routing and project ownership."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from group_chats import GroupStore


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GroupStore(self.temp.name)
        self.addCleanup(self.store.close)
        self.owner = self.store.create('Team', 'Alice', 'alice-pc', 'Alice PC')
        self.friend = self.join('Bob')
        self.other = self.join('Carol')

    def join(self, name):
        code = self.store.invite(self.owner['groupId'], self.owner['token'])['code']
        return self.store.join(self.owner['groupId'], code, name, name + '-pc', name + ' PC')

    def rpc(self, member, action, **body):
        return self.store.request(member['groupId'], member['token'], action, body)

    def channel(self, **body):
        return self.rpc(self.owner, 'channel-save', name='Build', **body)['channel']

    def share(self, **rules):
        result = self.rpc(self.owner, 'agents', models=[{
            'id': 'api:provider-a:6d6f64656c', 'key': 'builder', 'label': 'API model',
            'name': 'Builder', **rules}])
        return next(a for a in result['agents'] if a['name'] == 'Builder')

    def message(self, member=None, **body):
        return self.rpc(member or self.friend, 'message', text='Build the page',
                        requestId=body.pop('requestId', 'human-one'), **body)

    def test_general_migration_and_new_channel_keep_histories_separate(self):
        self.message()
        channel = self.channel()
        self.message(channelId=channel['id'], requestId='other-channel')
        general = self.rpc(self.friend, 'view')
        build = self.rpc(self.friend, 'view', channelId=channel['id'])
        self.assertEqual([c['id'] for c in general['channels']][0], 'general')
        self.assertEqual([m['id'] for m in general['messages']], [general['messages'][0]['id']])
        self.assertEqual(len(build['messages']), 1)
        self.assertNotEqual(general['messages'][0]['id'], build['messages'][0]['id'])
        self.assertEqual(build['channel']['id'], channel['id'])

    def test_private_direct_chat_excludes_unrelated_members_and_task_activity(self):
        agent = self.share()
        channel = self.rpc(self.friend, 'direct', memberIds=[], agentIds=[agent['id']])['channel']
        self.message(channelId=channel['id'], agentIds=[agent['id']])
        job = self.rpc(self.owner, 'claim')['task']
        self.assertEqual(job['channelId'], channel['id'])
        self.assertIn('Build the page', job['context'])
        self.rpc(self.owner, 'update', taskId=job['id'], leaseToken=job['leaseToken'],
                 status='completed', text='PRIVATE RESULT')
        self.assertEqual(self.rpc(self.friend, 'view', channelId=channel['id'])['messages'][-1]['text'], 'PRIVATE RESULT')
        with self.assertRaises(PermissionError):
            self.rpc(self.other, 'view', channelId=channel['id'])
        public = self.rpc(self.other, 'view')
        self.assertNotIn(channel['id'], [c['id'] for c in public['channels']])
        self.assertEqual(public['tasks'], [])
        self.assertEqual(public['messages'], [])
        self.assertEqual(public['plan'], [])

    def test_private_direct_channel_is_reused_and_cannot_be_converted_to_public(self):
        first = self.rpc(self.friend, 'direct', memberIds=[self.owner['memberId']])['channel']
        again = self.rpc(self.owner, 'direct', memberIds=[self.friend['memberId']])['channel']
        self.assertEqual(first['id'], again['id'])
        with self.assertRaises(PermissionError):
            self.rpc(self.other, 'channel-save', channelId=first['id'], name='Expose', private=False)

    def test_human_message_policies_are_deterministic_and_agent_replies_do_not_fan_out(self):
        agent = self.share(replyMode='always')
        self.message()
        self.message()  # HTTP retry
        job = self.rpc(self.owner, 'claim')['task']
        self.rpc(self.owner, 'update', taskId=job['id'], leaseToken=job['leaseToken'],
                 status='completed', text='Done @Builder')
        self.assertIsNone(self.rpc(self.owner, 'claim')['task'])
        self.assertEqual(len(self.rpc(self.owner, 'view')['tasks']), 1)
        self.message(requestId='humans-only', routing='humans-only')
        self.assertIsNone(self.rpc(self.owner, 'claim')['task'])

    def test_mentions_aliases_selected_only_and_member_channel_restrictions(self):
        channel = self.channel()
        agent = self.share(replyMode='mentions', aliases=['code'],
                           allowedMembers=[self.friend['memberId']], allowedChannels=[channel['id']])
        self.rpc(self.friend, 'message', text='@code ship it', requestId='mention', channelId=channel['id'])
        self.assertEqual(self.rpc(self.owner, 'claim')['task']['agentId'], agent['id'])
        with self.assertRaises(PermissionError):
            self.message(self.other, requestId='unauthorized', channelId=channel['id'], agentIds=[agent['id']])
        with self.assertRaises(PermissionError):
            self.message(requestId='wrong-channel', agentIds=[agent['id']])
        self.rpc(self.friend, 'message', text='@code no automatic call', requestId='selected-only',
                 channelId=channel['id'], routing='selected')
        self.assertEqual(len(self.rpc(self.owner, 'view', channelId=channel['id'])['tasks']), 1)

    def test_same_api_model_can_back_two_distinct_named_agents(self):
        result = self.rpc(self.owner, 'agents', models=[
            {'id': 'api:provider-a', 'key': 'writer', 'label': 'API', 'name': 'Writer'},
            {'id': 'api:provider-a', 'key': 'reviewer', 'label': 'API', 'name': 'Reviewer'}])
        agents = result['agents']
        self.assertEqual(len({a['id'] for a in agents}), 2)
        self.message(agentIds=[a['id'] for a in agents])
        self.assertEqual(len(self.rpc(self.owner, 'view')['tasks']), 2)

    def test_project_scope_is_frozen_for_queued_work_and_local_paths_are_not_advertised(self):
        project = self.rpc(self.owner, 'project-save', name='Website')['project']
        channel = self.channel(projectId=project['id'])
        agent = self.share()
        self.message(channelId=channel['id'], agentIds=[agent['id']])
        job = self.rpc(self.owner, 'claim')['task']
        self.assertEqual(job['projectId'], project['id'])
        self.assertEqual(job['channelId'], channel['id'])
        self.assertNotIn('folderPath', json.dumps(job))
        with self.assertRaises(ValueError):
            self.channel(projectId='unrelated-project')

    def test_cancel_only_by_origin_worker_or_owner_and_terminal_updates_stay_cancelled(self):
        agent = self.share()
        self.message(agentIds=[agent['id']])
        job = self.rpc(self.owner, 'claim')['task']
        with self.assertRaises(PermissionError):
            self.rpc(self.other, 'cancel', taskId=job['id'])
        self.rpc(self.friend, 'cancel', taskId=job['id'])
        view = self.rpc(self.owner, 'view')
        self.assertEqual(view['tasks'][0]['status'], 'cancelled')
        with self.assertRaises(PermissionError):
            self.rpc(self.owner, 'update', taskId=job['id'], leaseToken=job['leaseToken'],
                     status='completed', text='late response')

    def test_message_request_id_cannot_be_reused_in_another_channel(self):
        self.message()
        channel = self.channel()
        with self.assertRaises(ValueError):
            self.message(channelId=channel['id'])

    def test_narrowing_agent_access_retires_queued_work(self):
        agent = self.share(allowedMembers=[self.friend['memberId']])
        self.message(agentIds=[agent['id']])
        self.share(allowedMembers=[self.other['memberId']])
        self.assertIsNone(self.rpc(self.owner, 'claim')['task'])
        self.assertEqual(self.rpc(self.owner, 'view')['tasks'][0]['status'], 'cancelled')

    def test_delegation_preserves_frozen_project_after_channel_rebinding(self):
        first = self.rpc(self.owner, 'project-save', name='First')['project']
        second = self.rpc(self.owner, 'project-save', name='Second')['project']
        channel = self.channel(projectId=first['id'])
        values = self.rpc(self.owner, 'agents', models=[
            {'id': 'model-a', 'key': 'builder', 'name': 'Builder'},
            {'id': 'model-b', 'key': 'reviewer', 'name': 'Reviewer'}])['agents']
        builder = next(a for a in values if a['key'] == 'builder')
        reviewer = next(a for a in values if a['key'] == 'reviewer')
        self.message(channelId=channel['id'], agentIds=[builder['id']])
        self.rpc(self.owner, 'channel-save', channelId=channel['id'], name='Changed', projectId=second['id'])
        parent = self.rpc(self.owner, 'claim')['task']
        delegated = self.rpc(self.owner, 'agent-tool', taskId=parent['id'], leaseToken=parent['leaseToken'],
                             tool='delegate', args={'agentId': reviewer['id'], 'text': 'Review original project'})
        child = self.store._task(self.owner['groupId'], delegated['taskId'])
        self.assertEqual(parent['projectId'], first['id'])
        self.assertEqual(child['projectId'], first['id'])

    def test_owner_can_disable_delegation_and_choose_a_task_without_prior_history(self):
        agent=self.share(allowDelegation=False,historyMessages=0)
        self.rpc(self.friend,'message',text='OLD CHANNEL DETAIL',requestId='old')
        self.message(agentIds=[agent['id']])
        job=self.rpc(self.owner,'claim')['task']
        self.assertNotIn('OLD CHANNEL DETAIL',job['context'])
        with self.assertRaises(PermissionError):
            self.rpc(self.owner,'agent-tool',taskId=job['id'],leaseToken=job['leaseToken'],
                     tool='delegate',args={'agentId':agent['id'],'text':'Delegate'})

    def test_owner_role_priority_orders_queued_work(self):
        agents=self.rpc(self.owner,'agents',models=[
            {'id':'a','key':'low','name':'Low','priority':0},
            {'id':'b','key':'high','name':'High','priority':10}])['agents']
        self.message(agentIds=[next(a['id'] for a in agents if a['name']=='Low')])
        self.message(requestId='urgent',agentIds=[next(a['id'] for a in agents if a['name']=='High')])
        self.assertEqual(self.rpc(self.owner,'claim')['task']['agentName'],'High')

    def test_channel_delete_preserves_other_channels_and_requires_authority(self):
        channel=self.channel()
        self.message(channelId=channel['id'], requestId='in-channel')
        self.message(requestId='in-general')
        with self.assertRaises(PermissionError):
            self.rpc(self.other, 'channel-delete', channelId=channel['id'])
        with self.assertRaises(ValueError):
            self.rpc(self.owner, 'channel-delete', channelId='general')
        view=self.rpc(self.owner, 'channel-delete', channelId=channel['id'])
        self.assertEqual(view['channel']['id'], 'general')
        self.assertEqual([item['text'] for item in view['messages']], ['Build the page'])
        self.assertNotIn(channel['id'], [item['id'] for item in view['channels']])

    def test_channel_delete_refuses_active_tasks(self):
        channel=self.channel();agent=self.share()
        self.message(channelId=channel['id'],agentIds=[agent['id']])
        with self.assertRaisesRegex(ValueError, 'Cancel active'):
            self.rpc(self.owner, 'channel-delete', channelId=channel['id'])
        task=self.rpc(self.owner, 'claim')['task']
        self.rpc(self.owner, 'cancel', taskId=task['id'])
        self.rpc(self.owner, 'channel-delete', channelId=channel['id'])
        self.assertEqual(self.rpc(self.owner, 'view')['tasks'], [])

    def test_message_preferences_are_validated_and_passed_to_the_worker(self):
        agent=self.share()
        self.message(agentIds=[agent['id']], requestSettings={'effort':'high','approval':'ask-every-time'})
        task=self.rpc(self.owner, 'claim')['task']
        self.assertEqual(task['requestSettings'], {'effort':'high','approval':'ask-every-time'})
        with self.assertRaises(ValueError):
            self.message(requestId='bad-options',requestSettings={'approval':'bypass'})


if __name__ == '__main__':
    unittest.main()
