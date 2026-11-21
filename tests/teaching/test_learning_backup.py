import hashlib
import hmac
import io
import json
import secrets
import zipfile
import pytest
from core import learning_backup as backup, teaching, storage


def seed():
    token=teaching.grant('backup-mentor')
    row=teaching.submit({'title':'Backup learning procedure','topic':'Python','source':'owner test',
        'license':'owner-authorized','version':'1','content':'Test empty inputs and preserve actual failures.'},token)
    return token,row


def test_authenticated_restore_retrieves_lesson_and_revokes_old_authority(tmp_path,monkeypatch):
    token,row=seed();key=secrets.token_bytes(32)
    original=storage.DATA;raw,signature,manifest=backup.snapshot(key,offline=True)
    result=backup.restore(raw,signature,key,tmp_path/'restored')
    assert result['all_mentor_grants_revoked']
    monkeypatch.setattr(storage,'DATA',tmp_path/'restored')
    assert teaching.search('backup',owner=True)[0]['id']==row['id']
    with pytest.raises(PermissionError):teaching.search('',token)
    assert json.loads((storage.DATA/'household/engineering/index.json').read_text())[0]['access']=='owner'
    assert not (storage.DATA/'devices.json').exists()
    monkeypatch.setattr(storage,'DATA',original)
    assert teaching.search('',token)[0]['id']==row['id']
    with pytest.raises(ValueError,match='fresh'):backup.restore(raw,signature,key,tmp_path/'restored')


def test_tamper_wrong_key_and_no_offline_assertion_are_rejected(tmp_path):
    seed();key=secrets.token_bytes(32)
    with pytest.raises(ValueError,match='Offline'):backup.snapshot(key)
    raw,signature,_=backup.snapshot(key,offline=True)
    for changed,sig,k in [(raw+b'x',signature,key),(raw,signature,secrets.token_bytes(32))]:
        with pytest.raises(ValueError,match='authentication'):backup.restore(changed,sig,k,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('name',['../outside','household/teaching/.. /outside','household/teaching/x:stream','household/teaching/CON.txt','household/teaching/x\\escape','household/teaching/x.'])
def test_even_authenticated_unsafe_archive_paths_are_rejected(tmp_path,name):
    key=secrets.token_bytes(32);stream=io.BytesIO();payload=b'x'
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr(name,payload);z.writestr('manifest.json',json.dumps({'version':1,'members':{name:hashlib.sha256(payload).hexdigest()}}))
    raw=stream.getvalue();sig=hmac.new(key,raw,hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):backup.restore(raw,sig,key,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


@pytest.mark.parametrize('version,ready',[('6.20.0',False),('6.21.2',False),('6.21.4',True),('6.21.4rc1',False),('7.0.0',False)])
def test_pdf_dependency_gate(version,ready,monkeypatch):
    from core import teaching_import
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata,'version',lambda _:version)
    assert teaching_import.pdf_parser_ready()==ready


def test_actual_reviewed_pdf_extractor_or_vulnerable_version_block():
    from core.teaching_import import extract,pdf_parser_ready
    if not pdf_parser_ready():
        with pytest.raises(ValueError,match='PDF import blocked'):extract('input.pdf',b'%PDF untrusted bytes')
        return
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 250 Td (Reviewed parser integration) Tj ET');page[NameObject('/Contents')]=writer._add_object(stream)
    buffer=io.BytesIO();writer.write(buffer)
    assert 'Reviewed parser integration' in extract('input.pdf',buffer.getvalue())


def test_pdf_child_policy_does_not_load_application_credentials():
    import subprocess,sys
    from pathlib import Path
    result=subprocess.run([sys.executable,'-c',"import sys;from core.pdf_policy import parser_ready;assert 'core.config' not in sys.modules;assert 'core.storage' not in sys.modules;print(parser_ready())"],
        cwd=Path(__file__).resolve().parents[2],capture_output=True,text=True,check=True)
    assert result.stdout.strip() in {'True','False'}
