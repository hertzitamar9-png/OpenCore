"""Complete, bounded coding examples. Parsing never executes dataset code."""
import ast
import hashlib
import json
import re
from pathlib import Path
from rl_verifier import extract_function


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical_solution(source):
    tree=ast.parse(source)
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef):
        raise ValueError('Require one self-contained function')
    function=tree.body[0]
    # Type annotations/docstrings are presentation, not training targets. Keep
    # the complete executable body and retain raw source separately in records.
    function.returns=None
    for node in ast.walk(function):
        if isinstance(node,ast.arg): node.annotation=None
    if function.body and isinstance(function.body[0],ast.Expr) and isinstance(function.body[0].value,ast.Constant) and isinstance(function.body[0].value.value,str):
        function.body.pop(0)
    if not function.body: raise ValueError('Empty function')
    source=ast.unparse(tree)
    extract_function(source,function.name)
    return function.name,source


def literal_cases(source,name):
    cases=[]
    for statement in ast.parse(source).body:
        if not isinstance(statement,ast.Assert): continue
        expression=statement.test
        if not isinstance(expression,ast.Compare) or len(expression.ops)!=1 or not isinstance(expression.ops[0],ast.Eq): continue
        call=expression.left
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or call.func.id!=name or call.keywords: continue
        try:
            args=[ast.literal_eval(x) for x in call.args]
            expected=ast.literal_eval(expression.comparators[0])
            # The isolated worker uses JSON contracts. Reject sets, bytes,
            # nonfinite numbers, non-string mapping keys and tuple arguments
            # rather than silently changing a function's input semantics.
            def supported(x):
                if type(x) in (str,int,bool,type(None)): return True
                if type(x) is float: return x==x and abs(x)!=float('inf')
                if type(x) is list: return all(supported(y) for y in x)
                if type(x) is dict: return all(type(k) is str and supported(v) for k,v in x.items())
                return False
            if not supported(args) or not supported(expected): continue
            case={'args':args,'expected':expected}
            if len(json.dumps(case))>16000: continue
            if case not in cases: cases.append(case)
        except (ValueError,TypeError,SyntaxError): continue
    return cases


def training_prompt(task):
    return ('Return only one complete Python function. Use builtins, no imports, '
            'annotations, Markdown, examples or explanation.\n'+task)


def complete_tokens(tokenizer,task,solution,max_prompt=256,max_response=256):
    prompt=list(tokenizer.apply_chat_template([{'role':'user','content':training_prompt(task)}],
        tokenize=True,add_generation_prompt=True,enable_thinking=False,return_dict=False))
    response=list(tokenizer.encode(solution,add_special_tokens=False))
    if type(tokenizer.eos_token_id) is not int: raise ValueError('A single explicit end token is required')
    response.append(tokenizer.eos_token_id)
    if not prompt or not response or len(prompt)>max_prompt or len(response)>max_response:
        raise ValueError('Complete example exceeds recorded budget; do not truncate')
    return prompt,response


def prompt_key(prompt):
    return re.sub(r'\s+',' ',prompt.strip().lower())


def split_records(rows,exclude_prompts,train,validation,heldout):
    excluded={prompt_key(x) for x in exclude_prompts};seen_tasks=set();seen_code=set();unique=[]
    # Stable tie breaker ensures duplicate input order cannot change splits.
    for row in sorted(rows,key=lambda r:r['id']):
        key=prompt_key(row['prompt']);code=row['solution']
        if key in excluded or key in seen_tasks or code in seen_code: continue
        seen_tasks.add(key);seen_code.add(code)
        unique.append((hashlib.sha256(key.encode()).hexdigest(),row))
    unique.sort(key=lambda x:x[0]);rows=[row for _,row in unique]
    if len(rows)<train+validation+heldout: raise ValueError('Insufficient unique complete verified examples')
    return {'train':rows[:train],'validation':rows[train:train+validation],
            'heldout':rows[train+validation:train+validation+heldout]}
