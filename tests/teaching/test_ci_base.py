import pytest
from scripts.teaching_ci_base import select_base


SHA = 'a' * 40


def test_main_push_uses_previous_commit_even_when_origin_main_is_head():
    calls = []
    def git(*args):
        calls.append(args)
        return SHA + '\n'
    assert select_base('push', 'refs/heads/main', '', SHA, git) == SHA
    assert calls == [('rev-parse', '--verify', SHA + '^{commit}')]


def test_pull_request_uses_base_not_previous_push():
    assert select_base('pull_request', 'refs/pull/1/merge', SHA, 'invalid',
                       lambda *args: args[-1].removesuffix('^{commit}')) == SHA


def test_feature_push_checks_whole_task():
    calls = []
    def git(*args):
        calls.append(args)
        return SHA
    assert select_base('push', 'refs/heads/task', '', 'b' * 40, git) == SHA
    assert calls == [('merge-base', 'HEAD', 'origin/main')]


@pytest.mark.parametrize('before', ['', '0' * 40, '--help', 'a' * 39, 'z' * 40])
def test_invalid_main_history_fails_closed(before):
    def git(*args):
        pytest.fail('Invalid input must never reach git')
    with pytest.raises(ValueError):
        select_base('push', 'refs/heads/main', '', before, git)


def test_unavailable_commit_does_not_fall_back():
    def git(*args):
        raise RuntimeError('Missing history')
    with pytest.raises(RuntimeError):
        select_base('push', 'refs/heads/main', '', SHA, git)
