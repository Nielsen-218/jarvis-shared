"""Regression: streaming local speech starts before complete model output."""
import json
import pytest
from core.local_stream import SpeechAccumulator,read_openai_sse

def event(text,finish=None):
    return ("data: "+json.dumps({"choices":[{"delta":{"content":text},
       "finish_reason":finish}]})+"\n").encode()

def make():
    emitted=[]
    from core.offline import _unwrap
    return emitted,SpeechAccumulator(emitted.append,_unwrap)

def test_single_oversized_chunk_rejected_before_buffering_or_speaking():
    seen,acc=make()
    with pytest.raises(ValueError,match="too long"):
        acc.add("x"*200_001)
    assert acc.pending=="" and seen==[]

def test_first_complete_sentence_reaches_audio_before_stream_finishes():
    seen,acc=make()
    def packets():
        yield event("I can")
        assert seen==[]
        yield event(" help now. Here's")
        assert seen==["I can help now."]
        yield event(" the next step.")
        yield b"data: [DONE]\n"
    assert read_openai_sse(packets(),acc)
    assert seen==["I can help now.","Here's the next step."]

def test_partial_tokens_never_spoken_as_json_tool_call():
    seen,acc=make()
    packets=[event('{"name":"speak","parameters":'),
        event('{"text":"Hello. How are you?"}}'),
        b"data: [DONE]\n"]
    assert read_openai_sse(packets,acc)
    assert seen==["Hello.","How are you?"]
    assert not any("parameters" in s for s in seen)

def test_thinking_block_not_read_aloud():
    seen,acc=make()
    assert read_openai_sse([event("<think>wait. don't say. </think>Yes. OK."),
                           b"data: [DONE]\n"],acc)
    assert seen==["Yes.","OK."]

def test_json_without_natural_speech_fails_closed():
    seen,acc=make()
    assert not read_openai_sse([event('{"tool_call": {"name":"delete"}}'),
                               b"data: [DONE]\n"],acc)
    assert not seen

def test_cancel_midstream_prevents_new_speech_and_returns_already_spoken():
    emitted=[]
    cancelled=[False]
    acc=SpeechAccumulator(emitted.append,lambda x:x,lambda:cancelled[0])
    def packets():
        yield event("Ready. ")
        cancelled[0]=True
        yield event("Do not speak this.")
    assert read_openai_sse(packets(),acc,lambda:cancelled[0])
    assert emitted==["Ready."]

def test_partial_stream_failure_never_double_responds():
    emitted,acc=make()
    assert read_openai_sse([event("I can help. ")] ,acc)
    assert emitted==["I can help."]
    # Missing DONE intentionally drops unverified tail; no duplicate stream.

def test_split_and_unrecognized_sse_lines_ignored():
    emitted,acc=make()
    assert read_openai_sse([b": ping",b"event: message",b"data: {oops}",
                            event("Hello there."),event("",finish="stop")],acc)
    assert emitted==["Hello there."]

def test_fragmented_tool_speech_is_unwrapped_only_after_completion():
    seen,acc=make()
    def fragment(arguments):
        return ("data: "+json.dumps({"choices":[{"delta":{"tool_calls":[
            {"index":0,"function":{"arguments":arguments}}]}}]})+"\n").encode()
    def packets():
        yield fragment('{"text":"Hello')
        assert seen==[]
        yield fragment(' there."}')
        assert seen==[]
        yield b"data: [DONE]\n"
    assert read_openai_sse(packets(),acc)
    assert seen==["Hello there."]

def test_offline_uses_stream_flag_and_real_callback_without_paid_cloud(monkeypatch):
    from core import offline
    emitted=[]
    class Resp:
        def __init__(self):
            self.lines=[event("Quick first sentence. "),event("And one more."),
                        b"data: [DONE]\n"]
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def __iter__(self):return iter(self.lines)
    def open_local(req,timeout):
        request=json.loads(req.data)
        assert request["stream"] is True
        assert req.full_url.startswith("http://localhost:")
        return Resp()
    monkeypatch.setattr(offline,"available",lambda:True)
    monkeypatch.setattr(offline.urllib.request,"urlopen",open_local)
    monkeypatch.setattr(offline,"URL","http://localhost:1234/v1")
    monkeypatch.setitem(offline._probe,"model","local-test-model")
    assert offline.stream_reply("",[{"role":"user","content":"Say hello"}],
                                emitted.append,lambda:False)
    assert emitted==["Quick first sentence.","And one more."]
