import os
import pytest
from core.practice_runner import execute


@pytest.mark.skipif(os.name!='nt',reason='MSVC release-backend reliability test')
@pytest.mark.parametrize('iteration',range(8))
def test_native_release_compiler_is_msvc_and_repeatable(tmp_path,iteration):
    result=execute('long long solve(long long a,long long b){return a+b;}','cpp',
        [{'input':[-iteration,iteration+3],'expected':3},{'input':[12,-7],'expected':5}],tmp_path/'build')
    assert result['passed'],result
    assert result['compiler_family']=='msvc'
    assert result['compiler_path'].lower().endswith('cl.exe')
    assert all(c['windows_job_limit'] for c in result['commands'])
