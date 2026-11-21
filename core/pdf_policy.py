"""Dependency guard shared with the parser child. Imports no app configuration."""
import importlib.metadata
import re


def parser_ready():
    try:installed=importlib.metadata.version('pypdf')
    except importlib.metadata.PackageNotFoundError:return False
    return bool(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',installed) and
        (6,21,4)<=tuple(map(int,installed.split('.'))) < (7,0,0))
