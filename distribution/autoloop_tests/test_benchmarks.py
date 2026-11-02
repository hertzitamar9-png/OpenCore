from autoloop_vendor.benchmarks import Question,grade,split_questions,builtin_questions,load_questions
import pytest

def test_grading():
    assert grade(Question('','391','number'),'391')
    assert grade(Question('','391','number'),'Answer: 391')
    assert not grade(Question('','391','number'),'391 apples')
    assert grade(Question('','OtTaWa','exact'),'ottawa.')
    assert not grade(Question('','Ottawa','exact'),'Canada: Ottawa')

def test_questions_partition():
    questions=builtin_questions()
    dev,hold=split_questions(questions)
    assert len(questions)>=40
    assert set(q.prompt for q in dev).isdisjoint(q.prompt for q in hold)
    assert len(dev)+len(hold)==len(questions)

def test_small_benchmark_rejected(tmp_path):
    p=tmp_path/'sm.json'
    p.write_text('[{"prompt":"Hello", "answer":"Hi"}]')
    with pytest.raises(ValueError):load_questions(p)
