"""Bounded atomic replacement for short-lived Windows reader/indexer locks."""
import os
import time

def replace_atomic(source,target)->None:
    for attempt in range(5):
        try:
            os.replace(source,target)
            return
        except PermissionError as error:
            if attempt==4 or getattr(error,"winerror",None) not in {5,32,33}:
                raise
            time.sleep(.01*(2**attempt))
