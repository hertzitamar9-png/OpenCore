"""Small authored coding tasks; no benchmark test questions or solutions.

Tests are verifier inputs, never model prompts. Held-out tasks never enter RL.
These are pilot checks, not evidence of general coding superiority.
"""
def task(name,description,cases):
    return {'id':'echo-rl-v1/'+name,'name':name,'prompt':description,
            'cases':[{'args':args,'expected':answer} for args,answer in cases]}


TRAIN_TASKS=[
    task('coalesce_ranges','Implement coalesce_ranges(items). items is a list of inclusive integer [lo,hi] ranges with lo<=hi. Sort and merge overlapping or touching ranges. Return sorted [lo,hi] lists.',[
        ([[]],[]),([[[3,4],[1,2]]],[[1,4]]),([[[1,8],[2,3],[10,10]]],[[1,8],[10,10]]),
        ([[[5,5],[1,1],[3,3]]],[[1,1],[3,3],[5,5]]),([[[1,2],[2,5],[6,7]]],[[1,7]]),
        ([[[-4,-2],[-1,0],[3,6],[4,4]]],[[-4,0],[3,6]])]),
    task('window_counts','Implement window_counts(text,k). Return a dict mapping each length-k substring to its overlapping occurrence count. Return {} if k<=0 or k exceeds text length.',[
        (['aaaa',2],{'aa':3}),(['ababa',2],{'ab':2,'ba':2}),(['',1],{}),(['abc',0],{}),
        (['abc',4],{}),(['abc',3],{'abc':1}),(['אבאב',2],{'אב':2,'בא':1})]),
    task('rotate_groups','Implement rotate_groups(items,k). Split items into consecutive groups of at most k and rotate each group right by one. Flatten to a list. If k<=0 return a copy of items.',[
        ([[1,2,3,4,5],2],[2,1,4,3,5]),([[1,2,3,4,5],3],[3,1,2,5,4]),([[],2],[]),
        ([[1,2],5],[2,1]),([[1,2],0],[1,2]),([[1,2],1],[1,2])]),
    task('tidy_parts','Implement tidy_parts(parts). Drop empty strings and dots. Each .. cancels the preceding ordinary part; preserve .. if there is no ordinary part to cancel. Return the remaining list.',[
        ([['a','.','b','..','c']],['a','c']),([['..','..','a','..']],['..','..']),([[]],[]),
        ([['','a','..','..']],['..']),([['a','b','..','..']],[]),([['a','..','b','..','c']],['c'])]),
    task('stable_inverse','Implement stable_inverse(pairs). Each pair is [key,value] of strings. Return a dict from value to distinct keys in first-occurrence order. Repeated identical pairs must not add duplicate keys.',[
        ([[]],{}),([[['a','x'],['b','x'],['a','x']]],{'x':['a','b']}),
        ([[['a','x'],['a','y'],['b','x']]],{'x':['a','b'],'y':['a']}),
        ([[['',''],['a','']]],{'':['','a']}),([[['z','a']]],{'a':['z']}),
        ([[['b','x'],['a','x'],['b','y'],['b','x']]],{'x':['b','a'],'y':['b']})]),
    task('cap_runs','Implement cap_runs(items,limit). Keep at most limit elements from each consecutive run of equal items. Equal items in different runs are independent. Return [] if limit<=0.',[
        ([[1,1,1,2,1,1],2],[1,1,2,1,1]),([[1,1,2,2,1],1],[1,2,1]),([[],3],[]),
        ([[1,2],0],[]),([['a','a','b','b','b'],2],['a','a','b','b']),([[0,0,0],5],[0,0,0])]),
    task('select_rank','Implement select_rank(items,k). Return the kth largest distinct integer, with k starting at 1. Return None if k<=0 or there are fewer than k distinct values.',[
        ([[2,5,5,1],2],2),([[9,9],2],None),([[],1],None),([[1,2],0],None),
        ([[-8,-1,-4],2],-4),([[3,2,1],3],1),([[1],1],1)]),
    task('combine_deltas','Implement combine_deltas(events). Each event is [string,integer]. Casefold names, sum their deltas, and return a dict containing only names whose final total is nonzero.',[
        ([[]],{}),([[['A',2],['a',-2]]],{}),([[['A',2],['a',-1],['B',4]]],{'a':1,'b':4}),
        ([[['x',-4],['Y',0]]],{'x':-4}),([[['Straße',1],['STRASSE',2]]],{'strasse':3}),
        ([[['',1],['',-2],['Q',1]]],{'':-1,'q':1})]),
]

HELDOUT_TASKS=[
    task('first_missing','Implement first_missing(items). Return the smallest nonnegative integer absent from items. Ignore negative numbers and duplicates.',[
        ([[]],0),([[1,2]],0),([[0,1,3]],2),([[-1,0,0,2]],1),([[3,2,1,0]],4),([[0]],1)]),
    task('padded_zip','Implement padded_zip(left,right,fill). Return a list of [left_item,right_item] rows, padded with fill until both inputs are exhausted.',[
        ([[1,2],[3],0],[[1,3],[2,0]]),([[],[],None],[]),([[],['a','b'],'x'],[['x','a'],['x','b']]),
        ([[1],[2,3],None],[[1,2],[None,3]]),([[0],[0],-1],[[0,0]]),([[1,2],[],9],[[1,9],[2,9]])]),
    task('reverse_spans','Implement reverse_spans(items,size). Reverse each consecutive group of at most size, including the last partial group. Return a list. If size<=0 return a copy.',[
        ([[1,2,3,4,5],3],[3,2,1,5,4]),([[],2],[]),([[1,2],5],[2,1]),([[1,2,3],1],[1,2,3]),
        ([[1,2,3],0],[1,2,3]),([[1,2,3,4],2],[2,1,4,3])]),
    task('min_by_group','Implement min_by_group(pairs). Each pair is [string,integer]. Return a dict containing the minimum integer for every distinct string, preserving case.',[
        ([[]],{}),([[['a',3],['a',1],['b',9]]],{'a':1,'b':9}),([[['A',1],['a',2]]],{'A':1,'a':2}),
        ([[['x',-4],['x',-8]]],{'x':-8}),([[['',0]]],{'':0}),([[['a',3],['a',3]]],{'a':3})]),
]
