"""Spark's published thinking/function protocol as Chat Completions deltas.

Only complete declared function calls outside thinking and code fences become
tool calls. This module never executes a tool or repairs malformed syntax.
"""
import json
import re
import uuid


def thinking_enabled(payload):
    budget=payload.get('reasoning_budget_tokens')
    if budget is not None:return budget!=0
    return (payload.get('chat_template_kwargs') or {}).get('enable_thinking',True)


def reasoning_token_count(tokens,tokenizer,initial_thinking):
    thinking=initial_thinking;count=0
    opened=tokenizer.convert_tokens_to_ids('<think>')
    closed=tokenizer.convert_tokens_to_ids('</think>')
    for token in tokens:
        if token==opened:thinking=True
        elif token==closed:thinking=False
        elif thinking:count+=1
    return count


class SparkOutput:
    def __init__(self,tools,initial_thinking):
        self.tools={tool['function']['name']:tool['function'] for tool in (tools or []) if tool.get('type')=='function'}
        self.thinking=initial_thinking;self.buffer='';self.tool_body=None
        self.content='';self.reasoning='';self.calls=[]

    def _emit(self,events,channel,text):
        if not text:return
        if channel=='content':self.content+=text
        else:self.reasoning+=text
        events.append({channel:text})

    def _inside_code(self,prefix):
        combined=self.content+prefix
        fence=None
        for marker in re.findall(r'(?m)^\s*(```|~~~)',combined):
            if fence is None:fence=marker
            elif marker==fence:fence=None
        if fence is not None:return True
        inline=None
        for marker in re.findall(r'(?<!\\)`+',combined):
            if inline is None:inline=len(marker)
            elif len(marker)==inline:inline=None
        return inline is not None

    def _call(self,body):
        match=re.match(r'\s*([^\s<>]+)',body)
        if not match or match.group(1) not in self.tools:
            raise ValueError('Spark returned an undeclared function in its tool channel')
        name=match.group(1);schema=self.tools[name].get('parameters',{});properties=schema.get('properties',{})
        rest=body[match.end():].strip();arguments={}
        while rest:
            part=re.match(r'<arg_key>([^<>]+)</arg_key>\s*<arg_value>([\s\S]*?)</arg_value>',rest)
            if not part:raise ValueError('Spark returned malformed native function arguments')
            key,raw=part.groups()
            if key in arguments or (key not in properties and schema.get('additionalProperties') is False):
                raise ValueError('Spark returned a duplicate or undeclared function argument')
            kind=properties.get(key,{}).get('type')
            if kind=='string':value=raw
            else:
                try:value=json.loads(raw)
                except json.JSONDecodeError:
                    if kind is not None:raise ValueError('Spark returned invalid JSON for a typed function argument')
                    value=raw
            types={'string':lambda v:isinstance(v,str),'integer':lambda v:isinstance(v,int) and not isinstance(v,bool),
                   'number':lambda v:isinstance(v,(int,float)) and not isinstance(v,bool),'boolean':lambda v:isinstance(v,bool),
                   'array':lambda v:isinstance(v,list),'object':lambda v:isinstance(v,dict),'null':lambda v:v is None}
            if isinstance(kind,str) and kind in types and not types[kind](value):
                raise ValueError('Spark returned the wrong type for a function argument')
            if 'enum' in properties.get(key,{}) and value not in properties[key]['enum']:
                raise ValueError('Spark returned an unsupported function argument value')
            arguments[key]=value;rest=rest[part.end():].strip()
        if any(key not in arguments for key in schema.get('required',[])):
            raise ValueError('Spark returned a function call without required arguments')
        call={'index':len(self.calls),'id':'call_'+uuid.uuid4().hex,'type':'function',
              'function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False,allow_nan=False)}}
        self.calls.append(call);return {'tool_calls':[call]}

    def feed(self,text,final=False):
        self.buffer+=text;events=[]
        while self.buffer:
            if self.tool_body is not None:
                end=self.buffer.find('</tool_call>')
                if end<0:
                    if len(self.buffer)>256*1024:raise ValueError('Spark function call exceeded the protocol size limit')
                    if final:raise ValueError('Spark ended with an incomplete function call')
                    break
                events.append(self._call(self.buffer[:end]));self.buffer=self.buffer[end+12:];self.tool_body=None
                continue
            tags=['</think>'] if self.thinking else ['<think>','<tool_call>']
            found=[(self.buffer.find(tag),tag) for tag in tags if tag in self.buffer]
            if found:
                offset,tag=min(found);prefix=self.buffer[:offset]
                if not self.thinking and self._inside_code(prefix):
                    self._emit(events,'content',self.buffer[:offset+len(tag)]);self.buffer=self.buffer[offset+len(tag):]
                    continue
                self._emit(events,'reasoning_content' if self.thinking else 'content',prefix)
                self.buffer=self.buffer[offset+len(tag):]
                if tag=='</think>':self.thinking=False
                elif tag=='<think>':self.thinking=True
                else:self.tool_body='pending'
                continue
            keep=0
            if not final:
                for tag in tags:
                    keep=max(keep,max((length for length in range(1,len(tag)) if self.buffer.endswith(tag[:length])),default=0))
            part=self.buffer[:-keep] if keep else self.buffer
            self._emit(events,'reasoning_content' if self.thinking else 'content',part)
            self.buffer=self.buffer[-keep:] if keep else ''
            break
        if final and self.tool_body is not None:raise ValueError('Spark ended with an incomplete function call')
        return events

    def message(self):
        result={'role':'assistant','content':self.content}
        if self.reasoning:result['reasoning_content']=self.reasoning
        if self.calls:result['tool_calls']=[{key:value for key,value in call.items() if key!='index'} for call in self.calls]
        return result
