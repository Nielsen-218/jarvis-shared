"""Low-latency OpenAI-compatible local streaming to existing TTS queue."""
from __future__ import annotations
import json
import re
from typing import Callable,Iterable

_SENTENCE=re.compile(r"(?<=[.!?])\s+(?=\S)")
_THINK=re.compile(r"<think>.*?</think>",re.S)
_PREFIX_TAG=re.compile(r"^\s*(?:<think>|<\|)",re.I)

class SpeechAccumulator:
    def __init__(self,emit:Callable[[str],None],unwrap:Callable[[str],str],
                 cancelled:Callable[[],bool]=lambda:False):
        self.emit,self.unwrap,self.cancelled=emit,unwrap,cancelled
        self.pending=""
        self.emitted=0
        self.guard=False

    def _output(self,text:str)->None:
        cleaned=text.strip()
        if cleaned and not self.cancelled():
            self.emit(cleaned)
            self.emitted+=1

    def add(self,chunk:str)->None:
        if not isinstance(chunk,str) or not chunk or self.cancelled():return
        if len(self.pending)+len(chunk)>200_000:raise ValueError("Local model response too long")
        self.pending+=chunk
        if self.emitted==0 and (
            self.pending.lstrip().startswith(("{","[",chr(96))) or
            _PREFIX_TAG.search(self.pending) or
            self.pending.lstrip().startswith("<")):
            self.guard=True
        if self.guard:return
        parts=_SENTENCE.split(self.pending)
        if len(parts)>1:
            for sentence in parts[:-1]:self._output(sentence)
            self.pending=parts[-1]
        # A punctuation+whitespace boundary can arrive with the LAST token,
        # without any following word yet. Don't wait for another token just
        # to hand a complete sentence to the synthesis worker.
        if re.search(r"[.!?]\s+$",self.pending):
            self._output(self.pending)
            self.pending=""
        # Long streams without punctuation can still begin a sentence.
        if len(self.pending)>165:
            at=self.pending.rfind(" ",0,160)
            if at>65:
                self._output(self.pending[:at])
                self.pending=self.pending[at+1:]

    def finish(self)->bool:
        if self.cancelled():return self.emitted>0
        if self.guard:
            safe=self.unwrap(self.pending)
            safe=_THINK.sub("",safe).strip()
            if safe.startswith(("{","[","<think>","<|")):
                return self.emitted>0
            for part in _SENTENCE.split(safe):
                self._output(part)
        elif self.pending.strip():
            self._output(self.pending)
        self.pending=""
        return self.emitted>0

def read_openai_sse(lines:Iterable[bytes|str],acc:SpeechAccumulator,
                    cancelled:Callable[[],bool]=lambda:False)->bool:
    """Read OpenAI-compatible chat SSE; never emit partial JSON or tool calls."""
    done=False
    tool_arguments={}
    for line in lines:
        if cancelled():break
        if isinstance(line,bytes):
            line=line.decode("utf-8",errors="replace")
        line=line.strip()
        if not line or line.startswith(":"):continue
        if not line.startswith("data:"):continue
        payload=line[5:].strip()
        if payload=="[DONE]":
            done=True
            break
        try:data=json.loads(payload)
        except ValueError:continue
        if not isinstance(data,dict):continue
        if data.get("error"):
            raise ValueError("Local model stream error")
        choices=data.get("choices",[])
        if not isinstance(choices,list) or not choices:continue
        choice=choices[0]
        if not isinstance(choice,dict):continue
        delta=choice.get("delta") or {}
        if not isinstance(delta,dict):continue
        chunk=delta.get("content")
        if isinstance(chunk,str):acc.add(chunk)
        for call in delta.get("tool_calls") or []:
            if not isinstance(call,dict):continue
            function=call.get("function") or {}
            arguments=function.get("arguments") if isinstance(function,dict) else None
            if isinstance(arguments,str):
                index=call.get("index",0)
                if not isinstance(index,int):continue
                previous=tool_arguments.get(index,"")
                if len(previous)+len(arguments)>200_000:
                    raise ValueError("Local model response too long")
                tool_arguments[index]=previous+arguments
        if choice.get("finish_reason") is not None:
            done=True
            break
    if done:
        if not acc.pending and not acc.emitted:
            for arguments in tool_arguments.values():
                acc.add(arguments)
                break
        return acc.finish()
    return acc.emitted>0
