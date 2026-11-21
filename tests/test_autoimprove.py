"""Local code improvement never trusts model-supplied paths or shell commands."""
import pytest
from core.autoimprove import ALLOWED,validate_patch,endpoint,patch_filename

def test_one_exact_replacement_is_allowed():
    source="def speak():\n    return 'slow response time'\n"
    patch={"path":"core/local_stream.py",
           "old":"    return 'slow response time'",
           "new":"    return 'fast response time'",
           "rationale":"shorter reply"}
    path,updated=validate_patch(patch,source,"voice")
    assert path=="core/local_stream.py"
    assert "'fast response time'" in updated
    assert "slow response" not in updated

@pytest.mark.parametrize("path",[
    ".env","../.env","core/pcbridge.py","tests/test_local_stream.py",
    "main.py","data/owner/memory.json","/etc/passwd","core/offline.py/../.env"
])
def test_coder_cannot_change_secrets_running_config_or_tests(path):
    with pytest.raises(ValueError):
        validate_patch({"path":path,"old":"01234567890123456789",
                        "new":"some other source content"},"01234567890123456789","voice")

def test_coder_cannot_insert_more_fields_or_unanchored_patch():
    original="line that is repeated\nline that is repeated\n"
    with pytest.raises(ValueError):
        validate_patch({"path":"core/local_stream.py","old":"line that is repeated",
                        "new":"different source"} ,original,"voice")
    with pytest.raises(ValueError):
        validate_patch({"path":"core/local_stream.py","old":"line that is not actually here",
                        "new":"different source"},original,"voice")
    with pytest.raises(ValueError):
        validate_patch({"path":"core/local_stream.py","old":"line that is repeated",
                        "new":"different source","shell":"curl somewhere"},original,"voice")

def test_loopback_only_no_paid_cloud_or_other_machine():
    for url in ("https://api.openai.com/v1","http://example.com/v1",
                "http://192.168.1.2:1234/v1",
                "http://localhost:1234/v1?token=secret",
                "http://user:pass@localhost:1234/v1",
                "http://localhost:1234/admin"):
        with pytest.raises(ValueError):
            endpoint(url)
    assert endpoint("http://127.0.0.1:1234/v1")=="http://127.0.0.1:1234/v1"

def test_categories_forbid_running_entire_agent_unrestrained():
    assert "audio/tts.py" not in ALLOWED["voice"]
    assert "main.py" not in ALLOWED["learning"]
    with pytest.raises(ValueError):
        validate_patch({"path":"core/local_stream.py","old":"01234567890123456789",
                        "new":"changed"},"01234567890123456789","learning")
