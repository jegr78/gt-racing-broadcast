"""Explicit preview/confirmation shared by telemetry CLI and local HTTP callbacks."""
import copy
import base64
import tempfile
from pathlib import Path

import ai_agents
import ai_jobs
import ai_package
import ai_reports

REQUEST_KEYS={'profile','rec','session','laps','references','agent','model','template',
              'goal','questions','language','ui_language','confirm_preview'}


class ControlError(ValueError):
    def __init__(self,code,message):self.code=code;super().__init__(message)


def _translate(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except (ai_agents.AgentError,ai_jobs.JobError,ai_package.PackageError,ai_reports.ReportError) as e:
        raise ControlError(e.code,str(e)) from e


class Controller:
    def __init__(self,machine,profile_root,profile,source_factory,adapter_factory=ai_agents.Adapter,validate=None,enrich_source=None):
        self.machine=Path(machine).resolve();self.profile_root=Path(profile_root).resolve();self.profile=profile
        self.source_factory=source_factory;self.adapter_factory=adapter_factory;self.validate=validate;self.enrich_source=enrich_source
        self.settings=ai_agents.Settings(self.machine)
        self.runner=ai_jobs.Runner(self.machine,self.profile_root,profile)
        self.reports=ai_reports.Store(self.profile_root,profile,self.machine,source_factory,enrich_source)

    def selection(self,rec):
        settings=_translate(self.settings.read)
        if not settings['enabled']:raise ControlError('disabled','Enable optional analysis in machine settings first')
        source=_translate(self.source_factory,rec)
        if source.root!=str((self.profile_root/'telemetry-recordings').resolve()):raise ControlError('invalid_source','Recording belongs to another profile')
        sessions=[]
        for session in source.completed:
            rows=ai_package._rows(source,session)
            sessions.append(dict(session=session,laps=[dict(lap=r['lap'],usable=ai_package._usable(r),
                  reasons=r.get('reasons',[])+([] if ai_package._usable(r) else ['Incomplete capture or unusable trace timing'])) for r in rows if r['lap']>0]))
        return dict(ok=True,profile=self.profile,rec=source.name,sessions=sessions)

    def references(self,rec,session):
        settings=_translate(self.settings.read)
        if not settings['enabled']:raise ControlError('disabled','Enable optional analysis in machine settings first')
        try:session=int(str(session))
        except ValueError:raise ControlError('invalid_selection','Select a numeric GT7 session') from None
        source=_translate(self.source_factory,rec)
        if source.root!=str((self.profile_root/'telemetry-recordings').resolve()):raise ControlError('invalid_source','Recording belongs to another profile')
        if session not in source.completed:raise ControlError('incomplete_session','Select a completed GT7 session')
        candidates=[]
        for path in sorted((self.profile_root/'telemetry-recordings').glob('*.gt7rec'))[:100]:
            if len(candidates)>=100:break
            try:candidate=_translate(self.source_factory,path.name)
            except ControlError:continue
            candidates.append(candidate)
        suggestions=ai_package.suggest_references(source,session,candidates)
        return dict(ok=True,profile=self.profile,suggestions=[dict(rec=r['recording'],session=r['session'],lap=r['lap'],time_s=r['time_s'],
            kind='same-session reference' if r['id'].startswith(source.identity+'/session/'+str(session)+'/') else 'explicit comparable recording/session') for r in suggestions[:100]])

    def _prepare(self,payload):
        if not isinstance(payload,dict) or set(payload)-REQUEST_KEYS:
            raise ControlError('invalid_selection','Unknown analysis request fields')
        if not self.profile:
            raise ControlError('invalid_profile','Select a profile before preparing analysis')
        if payload.get('profile',self.profile)!=self.profile:
            raise ControlError('profile_changed','Active profile changed; prepare a new preview')
        settings=_translate(self.settings.read)
        if not settings['enabled']:
            raise ControlError('disabled','Enable optional AI analysis in machine settings first')
        agent_id=payload.get('agent') or settings['last'].get('agent')
        config=next((a for a in settings['agents'] if a['id']==agent_id),None)
        if config is None:raise ControlError('invalid_selection','Choose a registered agent configuration')
        model=payload.get('model',config['model'])
        if not isinstance(model,str) or not model.strip() or len(model)>200:
            raise ControlError('unavailable_model','Choose an explicit model name')
        name=payload.get('rec')
        if not isinstance(name,str) or not name:
            raise ControlError('invalid_selection','Choose one recording')
        source=_translate(self.source_factory,name)
        expected_root=str((self.profile_root/'telemetry-recordings').resolve())
        if source.root!=expected_root:
            raise ControlError('invalid_source','Selected recording belongs to another profile')
        raw_refs=payload.get('references',[])
        if not isinstance(raw_refs,list) or len(raw_refs)>20:
            raise ControlError('invalid_selection','Select at most 20 explicit reference laps')
        refs=[];sources={source.name:source}
        for ref in raw_refs:
            if not isinstance(ref,dict) or set(ref)!={'rec','session','lap'} or not isinstance(ref['rec'],str):
                raise ControlError('invalid_selection','References require recording, session and lap')
            candidate=sources.get(ref['rec']) or _translate(self.source_factory,ref['rec'])
            sources[candidate.name]=candidate
            refs.append((candidate,ref['session'],ref['lap']))
        package=_translate(ai_package.build,source,payload.get('session'),laps=payload.get('laps'),references=refs,
                           template=payload.get('template',settings['last'].get('template') or 'session-overview'),
                           goal=payload.get('goal',''),questions=payload.get('questions',''),
                           language=payload.get('language'),ui_language=payload.get('ui_language','en'),enrich_source=self.enrich_source)
        manifest=_translate(ai_package.preview,package,config['provider'])
        adapter=self.adapter_factory(copy.deepcopy(config))
        binding=dict(package=package['fingerprint'],agent=config,model=model.strip(),profile=self.profile)
        confirm=ai_package.fingerprint(binding)
        probe=adapter.probe()
        availability=dict(probe)
        try:_translate(adapter.check_execution,probe,model=model)
        except ControlError as e:
            availability.update(status=e.code,guidance=str(e))
        return package,manifest,adapter,model,confirm,availability

    def preview(self,payload):
        package,manifest,adapter,model,confirm,availability=self._prepare(payload)
        return dict(ok=True,profile=self.profile,confirm_preview=confirm,manifest=manifest,
                    availability=availability,can_start=availability['status']=='ready',
                    package_fingerprint=package['fingerprint'],requested_model=model,
                    agent={k:copy.deepcopy(adapter.config[k]) for k in ('id','name','provider','mode','personal')})

    def start(self,payload,background=True):
        if not isinstance(payload,dict) or not payload.get('confirm_preview'):
            raise ControlError('preview_required','Preview the selected data and explicitly confirm its fingerprint before starting')
        package,_manifest,adapter,model,confirm,availability=self._prepare(payload)
        if payload['confirm_preview']!=confirm:
            raise ControlError('preview_changed','Data or agent/model selection changed; inspect a new preview before starting')
        if availability['status']!='ready':
            raise ControlError(availability['status'],availability.get('guidance','Check the provider CLI'))
        with tempfile.TemporaryDirectory(prefix='racecast-ai-prepared-') as root:
            directory=Path(root)/'package';_translate(ai_package.write,package,directory,adapter.provider)
            if background:
                job=_translate(self.runner.start,package,directory,adapter,model=model,validate=self.validate)
                result=dict(ok=True,id=job,profile=self.profile,state='queued')
            else:
                result=_translate(self.runner.run,package,directory,adapter,model=model,validate=self.validate)
                result.pop('directory',None);result['ok']=result['state']=='completed'
        # Preferences are independent of profile artifacts. A concurrent settings
        # edit must not undo the completed admission or overwrite new configurations.
        try:
            self.settings.update_last(adapter.config['id'],model,package['template'])
        except ai_agents.AgentError:
            pass  # Preference persistence must not discard an admitted run.
        return result

    def job(self,job_id):return _translate(self.reports.get,job_id)

    def history(self,rec=None):return _translate(self.reports.history,rec)

    @staticmethod
    def _download(doc):
        raw=doc.pop('bytes')
        return dict(doc,ok=True,encoding='base64',content=base64.b64encode(raw).decode('ascii'))

    def export(self,job_id,kind,origin=None):return self._download(_translate(self.reports.export,job_id,kind,origin=origin))

    def export_preview(self,payload):
        package,_manifest,adapter,_model,confirm,_availability=self._prepare(payload)
        if payload.get('confirm_preview')!=confirm:
            raise ControlError('preview_changed','Preview the exact package before exporting it')
        raw=_translate(ai_reports.export_package,package,adapter.provider)
        return self._download(dict(bytes=raw,mime='application/zip',filename='racecast-input-'+package['fingerprint'][:12]+'.zip'))

    def status(self):
        result=self.runner.status(self.profile)
        if result['active']:
            result['active'].pop('directory',None)
            result['active'].pop('report',None)
        return dict(result,ok=True,profile=self.profile)

    def cancel(self,job_id):
        return dict(ok=True,cancelled=_translate(self.runner.cancel,job_id),profile=self.profile,
                    notice='Cancellation cannot recover already consumed subscription quota.')
