"""SenseNova's native template, streamed reasoning, and schema-checked XML calls."""
import copy
import json
import re
import uuid


def thinking(payload):
    effort=payload.get('opencore_reasoning_effort',payload.get('reasoning_effort'))
    options=payload.get('chat_template_kwargs') or {}
    return payload.get('reasoning_budget_tokens') != 0 and effort not in ('off','none') and options.get('enable_thinking') is not False


def template_options(payload):
    tools=payload.get('tools') or []
    if not isinstance(tools,list) or len(tools)>256:
        raise ValueError('Supply at most 256 function tools')
    names=set()
    for tool in tools:
        function=tool.get('function') if isinstance(tool,dict) else None
        if not isinstance(function,dict) or tool.get('type','function')!='function':
            raise ValueError('SenseNova accepts function tools')
        name=function.get('name')
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,127}',name) or name in names:
            raise ValueError('Supply distinct registered function names')
        names.add(name)
    choice=payload.get('tool_choice') or 'auto'
    if choice not in ('auto','none'):
        raise ValueError('This SenseNova adapter supports automatic tools or tool_choice=none')
    return {'enable_thinking':thinking(payload),'tools':tools if choice!='none' else []}


def preserve_tool_fields(clean,original):
    result=copy.deepcopy(clean)
    for target,source in zip(result,original):
        for key in ('tool_calls','tool_call_id','reasoning_content','name'):
            if key in source:target[key]=copy.deepcopy(source[key])
        for call in target.get('tool_calls') or []:
            function=call.get('function',call)
            if isinstance(function.get('arguments'),str):
                function['arguments']=json.loads(function['arguments'])
            if not isinstance(function.get('arguments',{}),dict):raise ValueError('Tool history arguments must be objects')
    return result


def messages_prompt(tokenizer,payload):
    messages=payload.get('messages')
    if not isinstance(messages,list) or not messages or len(messages)>256:
        raise ValueError('Supply bounded chat messages')
    clean=[]
    for message in messages:
        if not isinstance(message,dict) or message.get('role') not in ('system','developer','user','assistant','tool'):
            raise ValueError('Unsupported message role')
        content=message.get('content') or ''
        if isinstance(content,list):
            if any(not isinstance(item,dict) or item.get('type')!='text' for item in content):
                raise ValueError('Image inputs require the local vision processor')
            content='\n'.join(item.get('text','') for item in content)
        if not isinstance(content,str):raise ValueError('Supply text content')
        clean.append({**copy.deepcopy(message),'role':'system' if message['role']=='developer' else message['role'],'content':content})
    clean=preserve_tool_fields(clean,clean)
    return tokenizer.apply_chat_template(merge_instructions(clean),tokenize=False,add_generation_prompt=True,**template_options(payload))


def merge_instructions(messages):
    """The publisher template permits one system message, at the start."""
    system=[];conversation=[]
    for message in messages:
        if message['role'] in ('system','developer'):
            parts=message['content']
            if isinstance(parts,list):
                if any(part['type']!='text' for part in parts):raise ValueError('System instructions require text')
                parts='\n'.join(part['text'] for part in parts)
            system.append(parts)
        else:conversation.append(message)
    return ([{'role':'system','content':'\n\n'.join(system)}] if system else [])+conversation


def parse_call(raw,tools):
    from jsonschema import Draft202012Validator
    from referencing import Registry
    if len(raw)>262144:raise ValueError('The generated tool call exceeds the supported limit')
    match=re.fullmatch(r'\s*<function=([A-Za-z_][A-Za-z0-9_.:-]{0,127})>\s*(.*?)\s*</function>\s*',raw,re.S)
    if not match:raise ValueError('The model returned an invalid native function call; no action ran')
    name,body=match.groups()
    function=next((tool['function'] for tool in tools if tool['function']['name']==name),None)
    if function is None:raise ValueError('The model requested an unregistered tool; no action ran')
    schema=function.get('parameters',{'type':'object','properties':{}})
    if not isinstance(schema,dict):raise ValueError('The registered tool has no valid argument schema')
    properties=schema.get('properties',{})
    arguments={};cursor=0
    for item in re.finditer(r'<parameter=([A-Za-z_][A-Za-z0-9_.:-]{0,127})>(.*?)</parameter>',body,re.S):
        if body[cursor:item.start()].strip():raise ValueError('Invalid text between tool arguments')
        key,value=item.groups()
        if value.startswith('\n') and value.endswith('\n'):value=value[1:-1]
        if key in arguments:raise ValueError('Duplicate generated tool argument')
        argument_schema=properties.get(key,{})
        if not isinstance(argument_schema,dict):raise ValueError('Invalid registered argument schema')
        kind=argument_schema.get('type')
        # The native template writes string values verbatim, and serializes
        # numbers, booleans, arrays and objects as JSON. Never evaluate code.
        if kind=='string':arguments[key]=value
        else:
            try:arguments[key]=json.loads(value)
            except json.JSONDecodeError:
                if kind is None or isinstance(kind,list) and 'string' in kind:arguments[key]=value
                else:raise ValueError('The model returned invalid JSON for a typed argument') from None
        cursor=item.end()
    if body[cursor:].strip():raise ValueError('Unparsed generated tool arguments')
    Draft202012Validator.check_schema(schema)
    try:Draft202012Validator(schema,registry=Registry()).validate(arguments)
    except Exception as error:raise ValueError('Generated tool arguments do not match the registered schema; no action ran') from error
    return {'id':'call_'+uuid.uuid4().hex[:24],'type':'function',
            'function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False,separators=(',',':'))}}


class OutputStream:
    def __init__(self,payload):
        self.tools=template_options(payload)['tools']
        self.mode='reasoning_content' if thinking(payload) and 'prompt' not in payload else 'content'
        self.buffer='';self.call='';self.calls=[];self.parts={'content':[],'reasoning_content':[]}

    def feed(self,text,final=False):
        self.buffer+=text;output=[]
        while self.buffer:
            markers={'<think>':'reasoning_content','</think>':'content'}
            if self.mode=='tool':markers={'</tool_call>':'content'}
            elif self.mode=='content' and self.tools:markers['<tool_call>']='tool'
            found=[(self.buffer.find(marker),marker,mode) for marker,mode in markers.items() if marker in self.buffer]
            if found:
                index,marker,mode=min(found)
                segment=self.buffer[:index];self.buffer=self.buffer[index+len(marker):]
            else:
                keep=max((length for marker in markers for length in range(1,len(marker)) if self.buffer.endswith(marker[:length])),default=0) if not final else 0
                segment=self.buffer[:-keep] if keep else self.buffer
                self.buffer=self.buffer[-keep:] if keep else ''
            if self.mode=='tool':
                self.call+=segment
                if len(self.call)>262144:raise ValueError('The generated tool call is too large')
            elif segment:
                self.parts[self.mode].append(segment);output.append({self.mode:segment})
            if found:
                if self.mode=='tool':
                    if len(self.calls)>=32:raise ValueError('Too many generated tool calls')
                    self.calls.append(parse_call(self.call,self.tools));self.call=''
                self.mode=mode
            else:break
        if final:
            if self.mode=='tool':raise ValueError('The model stopped inside a tool call; no action ran')
            # Publish action deltas only after the entire generated call batch
            # passed parsing and schema validation.
            for index,call in enumerate(self.calls):
                output.append({'tool_calls':[{'index':index,**call}]})
        return output

    def message(self):
        result={'role':'assistant','content':''.join(self.parts['content'])}
        reasoning=''.join(self.parts['reasoning_content'])
        if reasoning:result['reasoning_content']=reasoning
        if self.calls:result['tool_calls']=self.calls
        return result
