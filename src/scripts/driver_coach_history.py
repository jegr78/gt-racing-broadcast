"""Private compact training checkpoints; recordings and speech buffers stay separate."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

import atomic_replace
import driver_coach_calibration as calibration
import gt7_context

MAX_BYTES=512*1024
MAX_RECORDS=32
RETENTION_S=90*86400
_LOCK=threading.RLock()
LOG=logging.getLogger('racecast.relay.coach')
_ID=re.compile(r'[0-9a-f]{32}')
_FIELDS={'id','training_id','generation','started_at','updated_at','ended_at','status','reason',
         'context','objective','baseline','reference','focus','worked_focus','progress','latest','source_ids',
         'source_fingerprints','reference_fingerprint'}
_FORBIDDEN={'trace','points','audio','transcript','credentials','provider','prompt','path'}


def _error(code='history_unavailable'):
    raise calibration.CoachError(code,'Private training history is unavailable' if code=='history_unavailable' else 'Training history changed; reload before deleting')


def _pick(value,keys):
    return {key:copy.deepcopy(value[key]) for key in keys if key in value} if isinstance(value,dict) else None


def fingerprint(facts):
    return hashlib.sha256(json.dumps(facts,sort_keys=True,allow_nan=False).encode()).hexdigest()


def compact(state,current,generation,started_at,now,proof=(),reference_fingerprint=None):
    """Project only current scalar/compact provenance, never a lap trace or utterance."""
    calibrated=state.get('calibration') or {}
    if not calibrated.get('training_id') or not calibrated.get('epoch'):
        return None
    trained=state.get('focus') or {}
    baseline=_pick(trained.get('baseline'),('id','lap_ids','time_s','range_s'))
    reference=_pick(calibrated.get('reference'),('id','source'))
    focus=_pick(trained.get('focus'),('id','skill','label','action','reviewed','section_id'))
    progress=_pick(trained.get('progress'),('result','sample_count','pace_improved','consistency_improved',
                                         'measure_range_s','measure','text'))
    latest=_pick(state.get('latest'),('lap_id','observation','interpretation','action','check'))
    ids=list((baseline or {}).get('lap_ids',[]))
    ids.extend(r.get('lap_id') for r in (trained.get('attempts') or [])[-3:])
    if reference:ids.append(reference.get('id'))
    if latest:ids.append(latest.get('lap_id'))
    result=dict(id=calibrated['epoch'],training_id=calibrated['training_id'],generation=generation,
                started_at=started_at,updated_at=now,ended_at=None,status='active',reason=None,
                context=calibration._context(current or {}),objective=calibrated['objective'],
                baseline=baseline,reference=reference,focus=focus,worked_focus=[focus] if focus else [],progress=progress,latest=latest,
                source_ids=list(dict.fromkeys(ident for ident in ids if calibration._text(ident)))[:8],
                reference_fingerprint=reference_fingerprint,source_fingerprints={})
    result['source_fingerprints']={ident:fingerprint(facts) for ident,facts in proof if ident in result['source_ids']}
    _validate_record(result)
    return result


def _validate_record(record):
    if not isinstance(record,dict) or set(record)!=_FIELDS:
        _error()
    if any(not isinstance(record[k],str) or not _ID.fullmatch(record[k]) for k in ('id','training_id','generation')):
        _error()
    if record['status'] not in ('active','stopped','interrupted','invalidated','restarted','revoked'):
        _error()
    if record['objective'] not in calibration.OBJECTIVES:
        _error()
    if any(type(record[k]) not in (int,float) or not 0<=record[k]<1e12 for k in ('started_at','updated_at')):
        _error()
    if record['ended_at'] is not None and (type(record['ended_at']) not in (int,float) or not 0<=record['ended_at']<1e12):
        _error()
    if record['reason'] is not None and not calibration._text(record['reason']):
        _error()
    if (not isinstance(record['context'],dict) or set(record['context'])!=set(calibration.CONTEXT)
            or not isinstance(record['source_ids'],list) or len(record['source_ids'])>8
            or not all(calibration._text(ident) for ident in record['source_ids'])):
        _error()
    if (not isinstance(record['source_fingerprints'],dict) or set(record['source_fingerprints'])-set(record['source_ids'])
            or not all(isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) for value in record['source_fingerprints'].values())
            or record['reference_fingerprint'] is not None and (not isinstance(record['reference_fingerprint'],str)
                or not re.fullmatch('[0-9a-f]{64}',record['reference_fingerprint']))
            or not isinstance(record['worked_focus'],list) or len(record['worked_focus'])>8):_error()
    shapes={'baseline':{'id','lap_ids','time_s','range_s'},'reference':{'id','source'},
            'focus':{'id','skill','label','action','reviewed','section_id'},
            'progress':{'result','sample_count','pace_improved','consistency_improved','measure_range_s','measure','text'},
            'latest':{'lap_id','observation','interpretation','action','check'}}
    for key,allowed in shapes.items():
        value=record[key]
        if value is not None and (not isinstance(value,dict) or set(value)-allowed):
            _error()
        if key in ('baseline','reference','focus','latest') and value is not None and set(value)!=allowed:_error()
    if any(not isinstance(value,dict) or set(value)!=shapes['focus'] for value in record['worked_focus']):_error()
    for focus in ([record['focus']] if record['focus'] else [])+record['worked_focus']:
        if (not all(calibration._text(focus[k],600 if k=='action' else 160) for k in ('id','skill','label','action'))
                or focus['skill'] not in ('braking','throttle','steering','gear','speed','line','pace','consistency')
                or type(focus['reviewed']) is not bool
                or focus['section_id'] is not None and not calibration._text(focus['section_id'])):_error()
    baseline=record['baseline']
    if baseline is not None and (not calibration._text(baseline['id']) or not isinstance(baseline['lap_ids'],list)
            or len(baseline['lap_ids'])!=2 or not all(calibration._text(ident) for ident in baseline['lap_ids'])
            or any(type(baseline[k]) not in (int,float) or not 0<=baseline[k]<1e9 for k in ('time_s','range_s'))):_error()
    reference=record['reference']
    if reference is not None and (not calibration._text(reference['id']) or reference['source'] not in ('personal','selected')):_error()
    progress=record['progress']
    if progress is not None and (not {'result','sample_count','pace_improved','consistency_improved','text'}.issubset(progress)
            or progress['result'] not in ('waiting','provisional','evidence_unavailable','repeated_improvement','repeated_regression','repeatability_maintained')
            or type(progress['sample_count']) is not int or not 0<=progress['sample_count']<=3
            or any(type(progress[k]) is not bool for k in ('pace_improved','consistency_improved'))
            or not calibration._text(progress['text'],600)):_error()
    latest=record['latest']
    if latest is not None and (not calibration._text(latest['lap_id'])
            or any(not calibration._text(latest[k],600) for k in ('observation','interpretation','check'))
            or latest['action'] is not None and not calibration._text(latest['action'],600)):_error()
    def bounded(value,depth=0):
        if depth>12:_error()
        if isinstance(value,dict):
            if any(not isinstance(k,str) or k in _FORBIDDEN for k in value):_error()
            for item in value.values():bounded(item,depth+1)
        elif isinstance(value,list):
            if len(value)>32:_error()
            for item in value:bounded(item,depth+1)
        elif isinstance(value,str) and (len(value)>1200 or any(ord(c)<32 for c in value)):_error()
    bounded(record)
    try:
        if len(json.dumps(record,allow_nan=False).encode())>8192:_error()
    except (ValueError,TypeError,RecursionError,OverflowError):
        _error()
    return record


class Store:
    def __init__(self,profile_root,clock=time.time):
        self.profile_root=Path(profile_root)
        self.directory=self.profile_root/'coach-history'
        self.clock=clock

    def _path(self,owner):
        if (not calibration.valid_owner(owner) or owner['profile']!=self.profile_root.name
                or not (owner['subject']=='local:producer' or owner['subject'].startswith('console:') and len(owner['subject'])>8)):
            _error('invalid_owner')
        name=hashlib.sha256(json.dumps(owner,sort_keys=True).encode()).hexdigest()
        return self.directory/(name+'.json')

    def _prepare(self):
        if self.profile_root.is_symlink() or self.directory.is_symlink():_error()
        self.directory.mkdir(parents=True,mode=0o700,exist_ok=True)
        os.chmod(self.directory,0o700)

    def _check_path(self,path):
        if (self.profile_root.is_symlink() or self.directory.is_symlink() or path.is_symlink()
                or path.parent.resolve()!=self.directory.resolve()):_error()

    def _load(self,path,owner):
        self._check_path(path)
        try:
            fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        except FileNotFoundError:
            return dict(version=1,owner=copy.deepcopy(owner),revision=0,entries=[],deleted=[])
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:_error()
            raw=stream.read(MAX_BYTES+1)
        self._check_path(path)
        if len(raw)>MAX_BYTES:_error()
        value=json.loads(raw)
        if (not isinstance(value,dict) or set(value)!={'version','owner','revision','entries','deleted'}
                or value['version']!=1 or value['owner']!=owner or type(value['revision']) is not int or value['revision']<0
                or not isinstance(value['entries'],list) or len(value['entries'])>MAX_RECORDS
                or not isinstance(value['deleted'],list) or len(value['deleted'])>MAX_RECORDS*2
                or not all(isinstance(ident,str) and _ID.fullmatch(ident) for ident in value['deleted'])):_error()
        for record in value['entries']:_validate_record(record)
        if len({r['id'] for r in value['entries']})!=len(value['entries']):_error()
        return value

    def _write(self,path,value):
        raw=json.dumps(value,sort_keys=True,allow_nan=False).encode('utf8')
        if len(raw)>MAX_BYTES:_error()
        self._check_path(path)
        fd,temp=tempfile.mkstemp(prefix='.coach-',dir=self.directory)
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            self._check_path(path)
            atomic_replace.replace(temp,path)
        finally:
            try:os.unlink(temp)
            except FileNotFoundError:pass  # Successful replacement already consumed the temporary file.

    def _edit(self,owner,operation):
        path=self._path(owner)
        try:
            with _LOCK:
                self._prepare()
                with gt7_context._file_lock(str(path)):
                    value=self._load(path,owner);before=copy.deepcopy(value)
                    cutoff=self.clock()-RETENTION_S
                    value['entries']=[r for r in value['entries'] if r['updated_at']>=cutoff]
                    result=operation(value)
                    value['entries']=sorted(value['entries'],key=lambda r:r['updated_at'],reverse=True)[:MAX_RECORDS]
                    if value!=before:
                        value['revision']+=1;self._write(path,value)
                    return result,dict(revision=value['revision'],entries=copy.deepcopy(value['entries']))
        except calibration.CoachError:
            raise
        except (OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
            _error()

    def read(self,owner,generation):
        if not isinstance(generation,str) or not _ID.fullmatch(generation):_error()
        def read(value):
            for record in value['entries']:
                if record['status']=='active' and record['generation']!=generation:
                    record.update(status='interrupted',reason='relay_interrupted',ended_at=max(self.clock(),record['updated_at']))
        return self._edit(owner,read)[1]

    def save(self,owner,record):
        _validate_record(record)
        def save(value):
            previous=next((r for r in value['entries'] if r['id']==record['id']),None)
            if (record['id'] in value['deleted'] or record['training_id'] in value['deleted']
                    or previous and (previous['status']!='active' or previous['generation']!=record['generation'])):
                return False
            if previous and record['updated_at']<previous['updated_at']:return False
            replacement=copy.deepcopy(record)
            if previous and replacement['latest'] is None:
                replacement['latest']=copy.deepcopy(previous['latest'])
                if replacement['latest']:
                    ident=replacement['latest']['lap_id']
                    replacement['source_ids']=list(dict.fromkeys(replacement['source_ids']+[ident]))[:8]
                    if ident in previous['source_fingerprints']:
                        replacement['source_fingerprints'][ident]=previous['source_fingerprints'][ident]
            worked={r['id']:r for r in (previous or {}).get('worked_focus',[])+replacement['worked_focus']}
            replacement['worked_focus']=list(worked.values())[-8:]
            while len(replacement['worked_focus'])>1 and len(json.dumps(replacement,allow_nan=False).encode())>8192:
                replacement['worked_focus'].pop(0)
            if record['latest'] is None and len(json.dumps(replacement,allow_nan=False).encode())>8192:
                replacement['latest']=None
            _validate_record(replacement)
            value['entries']=[r for r in value['entries'] if r['id']!=record['id']]+[replacement]
            return True
        return self._edit(owner,save)[0]

    def finish(self,owner,ident,status='stopped',reason='explicit_stop'):
        if status not in ('stopped','interrupted','invalidated','restarted','revoked') or not calibration._text(reason):_error()
        def finish(value):
            record=next((r for r in value['entries'] if r['id']==ident),None)
            if record and record['status']=='active':
                record.update(status=status,reason=reason,ended_at=max(self.clock(),record['updated_at']))
        return self._edit(owner,finish)[1]

    def delete(self,owner,expected,ident=None):
        if ident is not None and (not isinstance(ident,str) or not _ID.fullmatch(ident)):_error('invalid_body')
        def delete(value):
            if type(expected) is not int or expected!=value['revision']:_error('conflict')
            selected=[r['id'] for r in value['entries'] if ident is None or r['id']==ident]
            trainings=[r['training_id'] for r in value['entries'] if r['id'] in selected]
            value['deleted']=list(dict.fromkeys(value['deleted']+selected+trainings))[-MAX_RECORDS*2:]
            value['entries']=[r for r in value['entries'] if r['id'] not in selected]
        return self._edit(owner,delete)[1]


def expose(document,current,facts,reference_allowed):
    """A past checkpoint is historical evidence, never a current reference grant."""
    result=copy.deepcopy(document)
    lookup={row.get('id'):row for row in (current.get('laps') or [])[-512:] if isinstance(row,dict)}
    for record in result['entries']:
        worked_skills=list(dict.fromkeys(focus['skill'] for focus in record['worked_focus']))
        corrected=any(ident in lookup and fingerprint(facts(lookup[ident]))!=digest
                      for ident,digest in record['source_fingerprints'].items())
        reference=record['reference']
        denied=False
        if reference and reference['id'].startswith('r:'):
            try:denied=reference_allowed(reference['id'],record['reference_fingerprint']) is not True
            except (OSError,ValueError,KeyError,TypeError):denied=True
        record['evidence_status']='corrected' if corrected else 'reference_unavailable' if denied else 'historical'
        if corrected or denied:
            record.update(reference=None,reference_fingerprint=None,focus=None,worked_focus=[],progress=None)
            record['source_ids']=[ident for ident in record['source_ids'] if not ident.startswith('r:')]
            if corrected:record.update(baseline=None,latest=None)
            elif record['latest']:record['latest']=_pick(record['latest'],('lap_id','observation'))
        record.pop('source_fingerprints',None);record.pop('reference_fingerprint',None)
        worked=record['worked_focus']
        record['debrief']=dict(
            worked=('Previously worked skills: '+', '.join(worked_skills)+'. Exercise evidence is unavailable.' if (corrected or denied) and worked_skills else
                    '; '.join(focus.get('label','Exercise') for focus in worked)[:1000] or 'No retained exercise was scored.'),
            observed=(record.get('latest') or {}).get('observation') or 'No comparable completed-lap observation is available.',
            interpretation=(record.get('progress') or {}).get('text') or 'Evidence is unavailable; no improvement or cause is inferred.',
            next='Start fresh personal calibration before choosing the next exercise.' if not worked else
                 'After fresh personal calibration, consider repeating '+worked[-1]['skill']+'.')
    return result


def suggest(document,state,current):
    calibrated=state.get('calibration') or {}
    if calibrated.get('accepted') is not True or not state.get('active') or state.get('pending'):return None
    if (not all(current.get(k) for k in ('car_id','track_id','definition_fingerprint'))
            or current.get('settings_confirmed') is not True):return None
    keys=set(calibration.CONTEXT)-{'session_id','stint_id'}
    for record in document['entries']:
        focus=record.get('focus');progress=record.get('progress') or {}
        if (record['training_id']==calibrated['training_id'] or record['status'] not in ('stopped','interrupted')
                or record.get('evidence_status')!='historical' or not focus or progress.get('sample_count',0)<1
                or any(record['context'].get(key)!=current.get(key) for key in keys)):
            continue
        return dict(id=record['id'],skill=focus['skill'],text='Previous training suggests '+focus['skill']+
                    '. Apply only to this fresh calibration; historical times do not become its baseline.')
    return None


def _order(record):
    ended=record['ended_at'] if record['ended_at'] is not None else record['updated_at']
    return (ended,record['started_at'])


def merge_profile_histories(profile_root):
    """Fold every other owner's journal of one profile into the profile driver's journal.

    One Solo POV profile has one driver, so its trainings share one history. Older
    builds kept one file per Console subject; this folds them in once at coach start.
    An unreadable file is skipped and kept on disk. Returns the number of entries moved.
    """
    store=Store(profile_root)
    owner=dict(profile=store.profile_root.name,subject='local:producer')
    try:
        if store.profile_root.is_symlink() or store.directory.is_symlink() or not store.directory.is_dir():
            return 0
        target=store._path(owner)
        sources=sorted(path for path in store.directory.glob('*.json') if path.name!=target.name)
    except (OSError,calibration.CoachError):
        LOG.warning('coach history merge skipped: the history directory is unreadable')
        return 0
    if not sources:
        return 0
    loaded=[]
    for path in sources:
        try:
            with gt7_context._file_lock(str(path)):
                store._check_path(path)
                with open(path,'rb') as stream:raw=json.loads(stream.read(MAX_BYTES+1))
                theirs=raw.get('owner') if isinstance(raw,dict) else None
                if (not isinstance(theirs,dict) or store._path(theirs)!=path
                        or theirs['subject']=='local:producer'):_error()
                loaded.append((path,store._load(path,theirs)))
        except (calibration.CoachError,OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):
            LOG.warning('coach history merge skipped one unreadable owner journal; it stays on disk')
    if not loaded:
        return 0
    moved=0
    try:
        with _LOCK:
            store._prepare()
            with gt7_context._file_lock(str(target)):
                value=store._load(target,owner);before=copy.deepcopy(value)
                cutoff=store.clock()-RETENTION_S
                deleted=list(value['deleted'])
                for _path,other in loaded:deleted.extend(other['deleted'])
                deleted=list(dict.fromkeys(deleted))[-MAX_RECORDS*2:]
                hidden=set(deleted)
                merged={r['id']:r for r in value['entries']}
                for _path,other in loaded:
                    for record in other['entries']:
                        if record['id'] in hidden or record['training_id'] in hidden:continue
                        known=merged.get(record['id'])
                        if known is None or record['updated_at']>known['updated_at']:
                            merged[record['id']]=copy.deepcopy(record)
                kept=sorted((r for r in merged.values() if r['updated_at']>=cutoff),key=_order,reverse=True)[:MAX_RECORDS]
                own={r['id'] for r in value['entries']}
                moved=sum(1 for r in kept if r['id'] not in own)
                value.update(entries=kept,deleted=deleted)
                if value!=before:
                    value['revision']+=1;store._write(target,value)
    except (calibration.CoachError,OSError,ValueError,TypeError,KeyError,RecursionError,OverflowError):
        LOG.warning('coach history merge failed: the profile journal is unreadable; owner journals stay on disk')
        return 0
    for path,_other in loaded:
        try:
            path.unlink()
        except OSError:
            LOG.warning('coach history merge could not remove a merged owner journal')
            continue
        try:os.unlink(str(path)+'.lock')
        except OSError:pass  # The advisory lock file is harmless if another reader still holds it.
    return moved
