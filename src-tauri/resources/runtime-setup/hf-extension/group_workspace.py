"""Channel isolation and owner-selected routing for PC-hosted workspaces."""
import hashlib
import json
import re
import uuid

REPLY_MODES = ('mentions', 'always', 'manual')
WORKSPACE_ACTIONS = ('channel-save', 'channel-delete', 'project-save', 'direct', 'cancel')


def bounded(value, maximum=100, optional=False):
    if optional and value in (None, ''):
        return ''
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError('Enter text within the allowed length')
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Invalid workspace object ID')
    return value


def identifiers(values, maximum):
    if not isinstance(values, list) or len(values) > maximum:
        raise ValueError('Too many workspace choices')
    return list(dict.fromkeys(identifier(value) for value in values))


def boolean(value, key, default):
    result=value.get(key,default)
    if type(result) is not bool:raise ValueError('Choose an on/off setting for '+key)
    return result


def integer(value,key,default,minimum,maximum):
    result=value.get(key,default)
    if type(result) is not int or not minimum<=result<=maximum:raise ValueError('Invalid '+key)
    return result


def agent_config(value, *, local=False):
    if not isinstance(value, dict):
        raise ValueError('Expected an agent configuration')
    model_id = bounded(value.get('id'), 200)
    result = {'id': model_id, 'key': identifier(value.get('key') or hashlib.sha256(model_id.encode()).hexdigest()[:32]),
              'label': bounded(value.get('label') or model_id, 512),
              'name': bounded(value.get('name') or value.get('label') or model_id),
              'replyMode': value.get('replyMode', 'mentions'),
              'allowedMembers': identifiers(value.get('allowedMembers', []), 32),
              'allowedChannels': identifiers(value.get('allowedChannels', []), 100)}
    result.update(enabled=boolean(value,'enabled',True),historyMessages=integer(value,'historyMessages',20,0,100),
                  allowDelegation=boolean(value,'allowDelegation',True),acceptDelegation=boolean(value,'acceptDelegation',True),
                  priority=integer(value,'priority',5,0,10))
    if result['replyMode'] not in REPLY_MODES:
        raise ValueError('Choose mentions, every human message, or selected only')
    aliases = value.get('aliases', [])
    if not isinstance(aliases, list) or len(aliases) > 8:
        raise ValueError('Use at most eight mention aliases')
    result['aliases'] = list(dict.fromkeys(bounded(alias, 48) for alias in aliases))
    if local:
        result['instructions'] = bounded(value.get('instructions'), 2000, optional=True)
        result.update(catalogId=bounded(value.get('catalogId') or model_id,200),prepareIfMissing=boolean(value,'prepareIfMissing',False),
                      reasoningEffort=value.get('reasoningEffort','medium'),approvalMode=value.get('approvalMode','inherit'),
                      subagentsEnabled=boolean(value,'subagentsEnabled',True),maxSubagents=integer(value,'maxSubagents',3,1,8))
        if result['reasoningEffort'] not in ('off','low','medium','high','ultra') or result['approvalMode'] not in ('inherit','ask-every-time','allow-chat','allow-all'):
            raise ValueError('Choose a supported reasoning effort and tool approval policy')
        skills=value.get('skills',[])
        if not isinstance(skills,list) or any(skill not in ('computer-use','browser-use','chrome-control') for skill in skills):
            raise ValueError('Choose this agent\'s supported computer and browser tools')
        result['skills']=list(dict.fromkeys(skills))
        bindings = value.get('projectBindings', {})
        if not isinstance(bindings, dict) or len(bindings) > 100:
            raise ValueError('Use at most 100 local project bindings')
        result['projectBindings'] = {identifier(key): identifier(project) for key, project in bindings.items() if project}
    return result


class WorkspaceStore:
    def _workspace_init(self):
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS group_channels(group_id TEXT,id TEXT,data TEXT,PRIMARY KEY(group_id,id));
          CREATE TABLE IF NOT EXISTS group_projects(group_id TEXT,id TEXT,data TEXT,PRIMARY KEY(group_id,id));
        ''')
        if 'channel_id' not in [row[1] for row in self.db.execute('PRAGMA table_info(messages)')]:
            self.db.execute("ALTER TABLE messages ADD COLUMN channel_id TEXT NOT NULL DEFAULT 'general'")
        self.db.execute('CREATE INDEX IF NOT EXISTS message_channel ON messages(group_id,channel_id,seq)')

    def _channels(self, group, member):
        general = {'id': 'general', 'name': 'General', 'private': False, 'memberIds': [], 'agentIds': [], 'projectId': None}
        self.db.execute('INSERT OR IGNORE INTO group_channels VALUES(?,?,?)', (group, 'general', json.dumps(general)))
        values = [json.loads(row[0]) for row in self.db.execute(
            "SELECT data FROM group_channels WHERE group_id=? ORDER BY CASE WHEN id='general' THEN 0 ELSE 1 END,rowid", (group,))]
        return [item for item in values if not item['private'] or member['id'] in item['memberIds']]

    def _channel(self, group, member, value='general'):
        key = identifier(value or 'general')
        channel = next((item for item in self._channels(group, member) if item['id'] == key), None)
        if channel is None:
            raise PermissionError('This channel is unavailable to you')
        return channel

    def _projects(self, group):
        return [json.loads(row[0]) for row in self.db.execute('SELECT data FROM group_projects WHERE group_id=? ORDER BY rowid', (group,))]

    def _project(self, group, value):
        if value in (None, ''):
            return None
        project = next((item for item in self._projects(group) if item['id'] == identifier(value)), None)
        if project is None:
            raise ValueError('Choose a project from this group')
        return project['id']

    def _workspace_action(self, group, member, action, body):
        if action == 'project-save':
            if member['role'] != 'owner':
                raise PermissionError('Only the group owner can manage shared projects')
            key = identifier(body['projectId']) if body.get('projectId') else uuid.uuid4().hex
            projects = self._projects(group)
            if len(projects) >= 100 and not any(item['id'] == key for item in projects):
                raise ValueError('This group already has 100 projects')
            project = {'id': key, 'name': bounded(body.get('name'))}
            self.db.execute('INSERT OR REPLACE INTO group_projects VALUES(?,?,?)', (group, key, json.dumps(project)))
            return {'project': project, **self._view(group, member, channel_id=body.get('channelId', 'general'))}
        if action == 'direct':
            people = set(identifiers(body.get('memberIds', []), 31)) | {member['id']}
            agent_ids = identifiers(body.get('agentIds', []), 8)
            agents = [self._agent(group, key) for key in agent_ids]
            people.update(agent['memberId'] for agent in agents)
            active = {row['id']: row['name'] for row in self.db.execute('SELECT id,name FROM members WHERE group_id=? AND revoked=0', (group,))}
            if not people <= active.keys() or len(people) > 32 or (len(people) < 2 and not agents):
                raise ValueError('Choose a current member or shared agent')
            signature = hashlib.sha256(json.dumps([sorted(people), sorted(agent_ids)]).encode()).hexdigest()
            prior = next((item for item in self._channels(group, member) if item.get('directKey') == signature), None)
            channel = prior or {'id': uuid.uuid4().hex, 'private': True, 'memberIds': sorted(people), 'agentIds': agent_ids,
                                'name': ', '.join([a['name'] for a in agents] or [active[key] for key in sorted(people)]),
                                'directKey': signature, 'projectId': None, 'createdBy': member['id']}
            self.db.execute('INSERT OR REPLACE INTO group_channels VALUES(?,?,?)', (group, channel['id'], json.dumps(channel)))
            return {'channel': channel, **self._view(group, member, channel_id=channel['id'])}
        if action == 'channel-save':
            key = identifier(body['channelId']) if body.get('channelId') else uuid.uuid4().hex
            prior = self._channel(group, member, key) if body.get('channelId') else None
            if prior and (prior['private'] or member['role'] != 'owner' and prior.get('createdBy') != member['id']):
                raise PermissionError('This channel cannot be changed by you')
            if not prior and len(self._channels(group, member)) >= 100:
                raise ValueError('This group already has 100 channels')
            channel = {**(prior or {}), 'id': key, 'name': bounded(body.get('name')), 'private': False,
                       'memberIds': [], 'agentIds': [], 'projectId': self._project(group, body.get('projectId')),
                       'createdBy': prior.get('createdBy') if prior else member['id']}
            self.db.execute('INSERT OR REPLACE INTO group_channels VALUES(?,?,?)', (group, key, json.dumps(channel)))
            return {'channel': channel, **self._view(group, member, channel_id=key)}
        if action == 'channel-delete':
            key = identifier(body.get('channelId'))
            channel = self._channel(group, member, key)
            if key == 'general' or channel['private']:
                raise ValueError('The General channel and private chats cannot be deleted here')
            if member['role'] != 'owner' and channel.get('createdBy') != member['id']:
                raise PermissionError('Only the group owner or channel creator can delete it')
            if self.db.execute("SELECT 1 FROM tasks WHERE group_id=? AND status IN ('queued','running') AND json_extract(data,'$.channelId')=? LIMIT 1", (group, key)).fetchone():
                raise ValueError('Cancel active channel tasks before deleting this channel')
            self.db.execute("DELETE FROM plans WHERE group_id=? AND task_id IN (SELECT id FROM tasks WHERE group_id=? AND json_extract(data,'$.channelId')=?)", (group, group, key))
            self.db.execute("DELETE FROM tasks WHERE group_id=? AND json_extract(data,'$.channelId')=?", (group, key))
            self.db.execute('DELETE FROM messages WHERE group_id=? AND channel_id=?', (group, key))
            self.db.execute('DELETE FROM group_channels WHERE group_id=? AND id=?', (group, key))
            return self._view(group, member, channel_id='general')
        if action == 'cancel':
            job = self._task(group, body.get('taskId'))
            self._channel(group, member, job.get('channelId'))
            if member['id'] not in (job['requestedBy'], job['workerId']) and member['role'] != 'owner':
                raise PermissionError('Only the requester, worker or group owner can cancel this task')
            if job['status'] not in ('completed', 'failed', 'cancelled'):
                job.update(status='cancelled', error='Cancelled by ' + member['name'], updatedAt=self.clock())
                self._save_task(job)
            return self._view(group, member, channel_id=job.get('channelId'))
        raise ValueError('Unknown workspace action')

    def _agent_allowed(self, agent, member_id, channel):
        return agent.get('enabled',True) and (not agent.get('allowedMembers') or member_id in agent['allowedMembers']) and (
            not agent.get('allowedChannels') or channel['id'] in agent['allowedChannels']) and (
            not channel['private'] or agent['memberId'] in channel['memberIds'])

    def _targets(self, group, member, channel, content, body):
        explicit = identifiers(body.get('agentIds', []), 8)
        routing = body.get('routing', 'auto')
        if routing not in ('auto', 'selected', 'humans-only'):
            raise ValueError('Choose agent rules, selected agents, or humans only')
        if routing == 'humans-only':
            return []
        agents = [json.loads(row[0]) for row in self.db.execute('SELECT data FROM agents WHERE group_id=?', (group,))]
        selected = {key: self._agent(group, key) for key in explicit}
        for agent in selected.values():
            if not self._agent_allowed(agent, member['id'], channel):
                raise PermissionError('The agent owner has not allowed this caller or channel')
        if routing == 'auto':
            mentions = {}
            for agent in agents:
                if not self._agent_allowed(agent, member['id'], channel):
                    continue
                if agent.get('replyMode', 'mentions') == 'always' or agent['id'] in channel.get('agentIds', []):
                    selected[agent['id']] = agent
                if agent.get('replyMode') == 'manual':
                    continue
                for name in [agent['name'], *agent.get('aliases', [])]:
                    if re.search(r'(^|\s)@' + re.escape(name) + r'(?=\s|$|[.,!?])', content, re.I):
                        mentions.setdefault(name.casefold(), {})[agent['id']] = agent
            for matches in mentions.values():
                chosen = [a for key, a in matches.items() if key in explicit]
                if len(matches) > 1 and not chosen:
                    raise ValueError('Choose which shared agent should answer this mention')
                for agent in chosen or matches.values():
                    selected[agent['id']] = agent
        if len(selected) > 8:
            raise ValueError('Select up to eight agents for one message')
        return list(selected.values())
