import pytest
import json
from core.practice_language import parse
from core.practice_runner import execute


@pytest.mark.parametrize('source',[
    'import os\ndef solve(a,b): return 0',
    'def solve(a,b): return a.__class__',
    'def solve(a,b): return open("secret")',
    'def solve(a,b): return eval(a)',
    'def solve(a,b): return a**b',
    'def solve(a,b): return [0]*a',
    '@print\ndef solve(a,b): return a',
    'def solve(a=print(1),b=0): return a',
    'def solve(a,b): return globals()',
    'def solve(a,b): return unknown',
])
def test_python_capabilities_reject(source):
    with pytest.raises((ValueError,SyntaxError)): parse(source,'python')


@pytest.mark.parametrize('source',[
    '#include <fstream>\nint solve(int a,int b){return a;}',
    'int solve(int a,int b){system(a);return a;}',
    'int solve(int a,int b){return *(&a);}',
    'int solve(int a,int b){return "text";}',
    'int solve(int a,int b){asm(a);return a;}',
    'int solve(int a,int b){return unknown;}',
    'int solve(int a,int b){return a;} int other(){return 0;}',
    'int solve(int a,int b){return std::abs(a);}',
])
def test_cpp_capabilities_reject(source):
    with pytest.raises(ValueError): parse(source,'cpp')


@pytest.mark.parametrize('language,source',[
    ('python','def solve(a,b):\n    total=0\n    for i in range(a,b+1):\n        total+=i\n    return total'),
    ('cpp','long long solve(long long a,long long b){long long total=0;for(long long i=a;i<=b;i++){total+=i;}return total;}'),
])
def test_actual_compile_and_range_sum(language,source,tmp_path):
    result=execute(source,language,[{'input':[-3,3],'expected':0},{'input':[2,6],'expected':20}],tmp_path/'build')
    assert result['passed'],json.dumps(result,indent=2)
    assert all(x['exit_code']==0 for x in result['commands'])


@pytest.mark.parametrize('language,source',[
    ('python','def solve(a,b):\n    while b!=0:\n        a,b=b,a%b\n    return abs(a)'),
    ('cpp','long long solve(long long a,long long b){while(b!=0){long long t=a%b;a=b;b=t;}return abs(a);}'),
])
def test_actual_compile_and_gcd(language,source,tmp_path):
    result=execute(source,language,[{'input':[48,18],'expected':6},{'input':[0,0],'expected':0},
        {'input':[91,35],'expected':7}],tmp_path/'build')
    assert result['passed'],json.dumps(result,indent=2)


@pytest.mark.parametrize('language,source',[
    ('python','def solve(a,b):\n    while 1:\n        pass\n    return a'),
    ('cpp','int solve(int a,int b){while(1){}return a;}'),
])
def test_empty_infinite_loop_is_bounded(language,source,tmp_path):
    result=execute(source,language,[{'input':[1,2],'expected':3}],tmp_path/'build')
    assert not result['passed']
    assert 'step budget exceeded' in str(result)


@pytest.mark.parametrize('language,source',[
    ('python','def solve(a,b): return a*b'),
    ('cpp','int solve(int a,int b){return a*b;}'),
])
def test_integer_growth_is_bounded(language,source,tmp_path):
    result=execute(source,language,[{'input':[1000000000,1000000000],'expected':0}],tmp_path/'build')
    assert not result['passed'] and 'integer bound exceeded' in str(result)


def test_invalid_source_never_invokes_compiler(tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('must not run'))
    result=execute('def solve(a,b): return open(a)','python',[{'input':[1,2],'expected':3}],tmp_path/'invalid')
    assert not result['passed'] and result['commands']==[]
