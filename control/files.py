"""Bounded document ingestion and ID-based retrieval; documents never execute."""
import base64
import io
import json
from pathlib import Path
import uuid
import zipfile

MAX_BYTES=8*1024*1024
MIMES={'.txt':'text/plain; charset=utf-8','.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def _validate_document(name,encoded):
    suffix=Path(name).suffix.lower()
    if suffix not in MIMES:raise ValueError('Supported uploads are PDF, DOCX and UTF-8 TXT only.')
    if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+8:raise ValueError('Maximum file size is 8 MiB.')
    try:data=base64.b64decode(encoded,validate=True)
    except Exception:raise ValueError('File encoding is invalid.')
    if not data or len(data)>MAX_BYTES:raise ValueError('File must contain between 1 byte and 8 MiB.')
    if suffix=='.txt':
        try:text=data.decode('utf-8')
        except UnicodeError:raise ValueError('Text files must use UTF-8 encoding.')
        if any(ord(c)<32 and c not in '\r\n\t' for c in text):raise ValueError('Binary content is not accepted as text.')
    elif suffix=='.docx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members=archive.infolist()
                if len(members)>500 or sum(m.file_size for m in members)>32*1024*1024:raise ValueError('Document expands beyond the allowed size.')
                if not {'[Content_Types].xml','word/document.xml'}<=set(archive.namelist()):raise ValueError('Not a DOCX document.')
                for member in members:
                    if member.flag_bits & 1 or '..' in Path(member.filename).parts or member.filename.startswith('/'):
                        raise ValueError('Unsafe document archive.')
                    if any(s in member.filename.lower() for s in ('vbaproject','embeddings/','activex/')):raise ValueError('Macros and embedded objects are not allowed.')
                    if member.filename.endswith(('.xml','.rels')):
                        body=archive.read(member).lower()
                        if any(s in body for s in (b'<!doctype',b'<!entity',b'targetmode="external"',b"targetmode='external'")):
                            raise ValueError('External resources and XML entities are not allowed.')
                        import xml.etree.ElementTree as ET
                        try:document=ET.fromstring(archive.read(member))
                        except ET.ParseError:raise ValueError('Invalid document XML.')
                        for element in document.iter():
                            attributes={key.lower():value.lower() for key,value in element.attrib.items()}
                            if attributes.get('targetmode')=='external' or any('macroenabled' in v or 'vbaproject' in v for v in attributes.values()):
                                raise ValueError('External resources and macros are not allowed.')
        except zipfile.BadZipFile:raise ValueError('Invalid DOCX container.')
    else:
        if not data.startswith(b'%PDF-'):raise ValueError('File content does not match PDF.')
        from pypdf import PdfReader
        try:
            reader=PdfReader(io.BytesIO(data),strict=True)
            if reader.is_encrypted:raise ValueError('Password-protected PDFs are not supported.')
            if len(reader.pages)>300:raise ValueError('PDF exceeds 300 pages.')
            forbidden={'/JavaScript','/JS','/Launch','/EmbeddedFiles','/EmbeddedFile','/RichMedia','/XFA','/OpenAction','/AA','/SubmitForm','/ImportData'}
            visited=set()
            def inspect(value,depth=0):
                if depth>80:raise ValueError('PDF structure is too deeply nested.')
                if hasattr(value,'idnum'):
                    marker=(value.idnum,value.generation)
                    if marker in visited:return
                    visited.add(marker);value=value.get_object()
                if isinstance(value,dict):
                    if forbidden.intersection(value) or str(value.get('/S','')) in forbidden:raise ValueError('Active PDF content is not supported.')
                    for key,item in value.items():
                        if key!='/Parent':inspect(item,depth+1)
                elif isinstance(value,list):
                    for item in value:inspect(item,depth+1)
            inspect(reader.trailer)
        except ValueError:raise
        except Exception:raise ValueError('PDF could not be safely validated.')
    return suffix,data


def validate_document(name,encoded):
    # Parsers handle untrusted documents in a bounded child process.
    if Path(name).suffix.lower()=='.txt':return _validate_document(name,encoded)
    if not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+8:raise ValueError('Maximum file size is 8 MiB.')
    import subprocess,sys
    try:
        result=subprocess.run([sys.executable,'-m','control.files',Path(name).suffix.lower()],input=encoded.encode('ascii'),capture_output=True,timeout=15)
    except (subprocess.TimeoutExpired,UnicodeError):raise ValueError('Document validation exceeded its time or encoding limit.')
    if result.returncode:raise ValueError('Document rejected: invalid, active, unsupported or exceeds parser limits.')
    return Path(name).suffix.lower(),base64.b64decode(encoded,validate=True)


def upload_cv(store,root,body):
    suffix,data=validate_document(str(body.get('filename','')),body.get('content_b64',''))
    cid=str(uuid.uuid4());folder=root/'candidate/cv_variants';folder.mkdir(parents=True,exist_ok=True)
    target=folder/(cid+suffix)
    import os
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data)
    name=str(body.get('name') or Path(str(body.get('filename'))).name)[:200]
    store.add_cv({'id':cid,'name':name,'role_type':str(body.get('role_type',''))[:200],'variant':str(body.get('variant',''))[:200],
                  'notes':str(body.get('notes',''))[:2000],'file_path':str(target),'file_type':suffix[1:]})
    store.add_action('Review facts from uploaded CV','review_uploaded_cv','Personally confirm any claims before using them.',payload={'cv_id':cid})
    return {'status':'UPLOADED','id':cid,'message':'Stored as an untrusted document. No claims were confirmed.'}


def read_registered(store,root,kind,identity):
    with store._connect() as con:
        if kind=='reports':row=con.execute('SELECT path FROM reports WHERE id=?',(int(identity),)).fetchone()
        elif kind=='cvs':row=con.execute('SELECT file_path FROM cv_profiles WHERE id=?',(identity,)).fetchone()
        else:raise ValueError('Unsupported file category.')
    if not row:raise FileNotFoundError('File record does not exist.')
    # Writers use private state, while ROOT identifies application assets. They
    # are deliberately separate in installed instances.
    import os
    root = Path(root).absolute()
    configured = Path(os.environ.get('CHIEF_STATE_ROOT', str(store.path.resolve().parent)))
    if not configured.is_absolute():
        raise PermissionError('Private storage root must be absolute.')
    state = configured.absolute()
    if not store.path.resolve().is_relative_to(state.resolve()):
        raise PermissionError('Private storage does not match this installation.')
    bases = (state, root) if kind == 'reports' else (root,)
    folders = ('logs', 'notifications/outbox') if kind == 'reports' else ('candidate/cv_variants',)
    roots = [base / folder for base in bases for folder in folders]
    target = Path(row[0])
    if '..' in target.parts:
        raise PermissionError('File is outside its permitted storage area.')
    candidates = [target] if target.is_absolute() else [base / target for base in bases]
    resolved = None
    for candidate in candidates:
        # Do not follow intermediate directory links or Windows junctions either.
        if any(item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
               for item in (candidate, *candidate.parents)):
            raise PermissionError('Linked storage paths are not permitted.')
        checked = candidate.resolve()
        if any(checked.is_relative_to(folder.absolute()) for folder in roots):
            if resolved is None or checked.is_file():
                resolved = checked
            if checked.is_file():
                break
    if resolved is None:
        raise PermissionError('File is outside its permitted storage area. Review this installation’s report storage; old paths are not automatically relocated.')
    if not resolved.is_file():raise FileNotFoundError('The recorded file is missing from storage.')
    if resolved.stat().st_size>MAX_BYTES:raise ValueError('File exceeds the download limit.')
    if resolved.suffix.lower() not in (set(MIMES)|{'.html','.json','.csv'}):raise ValueError('Unsupported download format.')
    return resolved.read_bytes(),resolved.suffix.lower()


def report_text(data,suffix):
    text=data.decode('utf-8')
    if suffix=='.html':
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self):super().__init__();self.parts=[];self.skip=0
            def handle_starttag(self,tag,attrs):
                if tag in ('script','style'):self.skip+=1
            def handle_endtag(self,tag):
                if tag in ('script','style'):self.skip=max(0,self.skip-1)
            def handle_data(self,value):
                if not self.skip:self.parts.append(value)
        parser=Text();parser.feed(text);text='\n'.join(parser.parts)
    return text


def pdf_report(text):
    from html import escape
    from playwright.sync_api import sync_playwright
    if len(text)>500000:raise ValueError('Report is too large for PDF; download TXT instead.')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();page.route('**/*',lambda route:route.abort())
            page.set_content('<meta charset="utf-8"><style>body{font:11px sans-serif}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Chief Agent report</h1><pre>'+escape(text)+'</pre>')
            return page.pdf(format='A4',margin={'top':'18mm','bottom':'18mm','left':'15mm','right':'15mm'})
        finally:browser.close()


if __name__=='__main__':
    import sys
    try:
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(8,8))
        value=sys.stdin.buffer.read(MAX_BYTES*4//3+9).decode('ascii')
        _validate_document('document'+sys.argv[1],value)
    except Exception:raise SystemExit(1)
