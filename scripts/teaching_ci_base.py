"""Select the complete task diff without comparing a main push to itself."""
import os
import re
import subprocess


def select_base(event, ref, pull_base, before, git):
    def commit(value):
        if not re.fullmatch(r'[0-9a-fA-F]{40}', value or '') or set(value) == {'0'}:
            raise ValueError('A nonzero commit SHA is required')
        return git('rev-parse', '--verify', value + '^{commit}').strip()

    if event == 'pull_request':
        return commit(pull_base)
    if event == 'push' and ref == 'refs/heads/main':
        # Reject missing/zero history: silently choosing HEAD would bypass checks.
        return commit(before)
    return git('merge-base', 'HEAD', 'origin/main').strip()


if __name__ == '__main__':
    def git(*args):
        return subprocess.check_output(['git', *args], text=True)
    print(select_base(os.environ.get('GITHUB_EVENT_NAME', ''),
                      os.environ.get('GITHUB_REF', ''),
                      os.environ.get('TEACHING_PR_BASE', ''),
                      os.environ.get('TEACHING_PUSH_BEFORE', ''), git))
