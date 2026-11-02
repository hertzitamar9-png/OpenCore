"""Deterministic, locally graded question set. Not equivalent to human evaluation."""
from __future__ import annotations
import json
import random
import re
import unicodedata
import math
from dataclasses import dataclass
from pathlib import Path

# Small bundled smoke/selection suite. Users should supply diverse independent
# private evaluation datasets for meaningful generalization claims.
_ITEMS = [
 ('What is 17 * 23?', '391','number'),
 ('What is 144 divided by 12?', '12','number'),
 ('What is 15% of 240?', '36','number'),
 ('A car travels 90 km in 1.5 hours. What is its average speed in km/h?', '60','number'),
 ('What is 19 squared?', '361','number'),
 ('Compute 2^10.', '1024','number'),
 ('What is 7 factorial?', '5040','number'),
 ('What is the next number: 2, 6, 12, 20, 30, ?', '42','number'),
 ('Solve for x: 3x + 7 = 31.', '8','number'),
 ('Compute the greatest common divisor of 84 and 126.', '42','number'),
 ('What is the sum of integers from 1 through 20?', '210','number'),
 ('A rectangle has sides 7 and 9. What is its area?', '63','number'),
 ('How many minutes are in 2.75 hours?', '165','number'),
 ('Compute 11^3.', '1331','number'),
 ('What is 13 * 17?', '221','number'),
 ('A shop has 80 items and sells 25%. How many items remain?', '60','number'),
 ('What is binary 101101 in decimal?', '45','number'),
 ('What is the remainder when 100 is divided by 9?', '1','number'),
 ('Convert 0.375 to a simplified fraction. Format a/b.', '3/8','exact'),
 ('What is the median of 1, 3, 9, 12, 20?', '9','number'),
 ('If all dax are red and no red things are blue, can a dax be blue? Answer yes or no.', 'no','exact'),
 ('A is taller than B. B is taller than C. Who is shortest? Answer A, B, or C.', 'C','exact'),
 ('Unscramble the letters in "LEPPA" to a common fruit.', 'apple','exact'),
 ('What is the third letter in the word "algorithm"?', 'g','exact'),
 ('If today is Monday, what day will it be 10 days from now?', 'Thursday','exact'),
 ('What comes next in this sequence of letters: A, C, F, J, O, ?', 'U','exact'),
 ('If each of 6 people shakes hands once with every other person, how many handshakes occur?', '15','number'),
 ('Which number does not belong: 2, 3, 5, 9, 11? Answer only the number.', '9','number'),
 ('A train has 4 cars with 12 seats each; 17 seats are empty. How many seats are occupied?', '31','number'),
 ('In Python, what is the result of len(set([1,1,2,3,3]))?', '3','number'),
 ('In Python 3, what is 7 // 2?', '3','number'),
 ('In Python, what is the value of bool([])? Reply true or false.', 'false','exact'),
 ('In Python, what is list(range(2, 8, 2))? Answer as a Python list.', '[2, 4, 6]','exact'),
 ('In Python, what does "a,b,c".split(",")[1] return?', 'b','exact'),
 ('In Python, what is sum(i*i for i in range(4))?', '14','number'),
 ('What is the chemical symbol for gold?', 'Au','exact'),
 ('What is the capital of Canada?', 'Ottawa','exact'),
 ('Which planet is known as the Red Planet?', 'Mars','exact'),
 ('What is the SI unit of electric current?', 'ampere','exact'),
 ('What gas do plants primarily absorb during photosynthesis?', 'carbon dioxide','exact'),
 ('How many sides does a dodecagon have?', '12','number'),
 ('What is the square root of 2025?', '45','number'),
 ('Which is larger: 0.29 or 0.3? Answer only the number.', '0.3','number'),
 ('What is 36% of 250?', '90','number'),
 ('How many bits are in 6 bytes?', '48','number'),
 ('What is the smallest prime larger than 29?', '31','number'),
 ('What is the remainder of 2^10 divided by 7?', '2','number'),
 ('How many unique letters are there in the word "mississippi"?', '4','number'),
]

@dataclass(frozen=True)
class Question:
    prompt:str
    answer:str
    kind:str='exact'


def builtin_questions():
    return [Question(*x) for x in _ITEMS]


def load_questions(path: str|Path|None):
    if not path:
        return builtin_questions()
    path=Path(path)
    if path.stat().st_size>16*1024*1024:
        raise ValueError('Benchmark exceeds the 16 MiB limit')
    if path.suffix.lower() == '.jsonl':
        data=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    else:
        data=json.loads(path.read_text(encoding='utf-8'))
    return validate_questions(data)


def validate_questions(data):
    if not isinstance(data,list) or not 20<=len(data)<=10000:
        raise ValueError('A benchmark needs 20 to 10,000 objective questions')
    result=[]
    for i,item in enumerate(data):
        try:
            if not isinstance(item,dict):raise ValueError('Expected a question object')
            q=Question(item['prompt'],item['answer'],item.get('kind','exact'))
            if not isinstance(q.prompt,str) or not isinstance(q.answer,str) or not 1<=len(q.prompt.strip())<=8192 or not 1<=len(q.answer.strip())<=2048:
                raise ValueError('Use bounded nonempty prompt and answer strings')
            if q.kind not in ('exact','number'):raise ValueError('Only exact and number objective grading is supported')
            if q.kind=='number' and not math.isfinite(float(q.answer.replace(',',''))):raise ValueError('Expected a finite numeric answer')
            result.append(q)
        except Exception as e:
            raise ValueError(f'Invalid benchmark question at index {i}: {e}') from e
    if len(result)<20:
        raise ValueError('A meaningful optimization run requires at least 20 test questions')
    if len({_normal(q.prompt) for q in result})!=len(result):
        raise ValueError('Benchmark contains duplicate prompts')
    return result


def _normal(s:str)->str:
    return ' '.join(unicodedata.normalize('NFKC',s).strip().casefold().split()).strip(' .!"\'`')


def grade(q:Question, response:str)->bool:
    s=response.strip()
    # A common 'final answer' convention, accepted without accepting a verbose
    # coincidental mention elsewhere in the answer.
    m=re.search(r'(?im)^\s*(?:final answer|answer)\s*:\s*(.+?)\s*$',s)
    if m:
        s=m.group(1)
    s=s.strip(' .!"\'`')
    if q.kind=='number':
        def val(v):
            v=v.strip().replace(',','')
            try: return float(v)
            except ValueError: return None
        a,b=val(s),val(q.answer)
        return a is not None and b is not None and abs(a-b)<1e-8
    return _normal(s)==_normal(q.answer)


def split_questions(questions:list[Question],fraction=0.35,seed=117):
    items=questions[:]
    random.Random(seed).shuffle(items)
    num=max(8,round(len(items)*fraction))
    return items[num:],items[:num]


def prompt_for(q:Question)->str:
    return q.prompt + '\n\nReply with ONLY the final answer. No explanation or extra text.'


def benchmark_schema():
    return {'type':'array','minItems':20,'maxItems':10000,'items':{'type':'object',
            'required':['prompt','answer'],'properties':{'prompt':{'type':'string','minLength':1,'maxLength':8192},
            'answer':{'type':'string','minLength':1,'maxLength':2048},'kind':{'enum':['exact','number'],'default':'exact'}}}}
