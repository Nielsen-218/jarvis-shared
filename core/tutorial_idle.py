"""Optional owner queue: at most two source studies/day, zero background model GPU."""
import json
import time
from datetime import datetime, timezone
from core import research, users, teaching


def configure(enabled=None, topic=None):
    if users.get(users.current()).get('role')!='owner':raise PermissionError('Owner queue only')
    if enabled is not None and type(enabled) is not bool:raise ValueError('Enable flag must be boolean')
    if topic is not None and (not isinstance(topic,str) or not 3<=len(topic)<=200 or teaching.contains_credentials(topic)):
        raise ValueError('Use a short public topic without secrets')
    with research.database() as db:
        row=db.execute("SELECT value FROM settings WHERE key='idle_queue'").fetchone()
        queue=json.loads(row[0]) if row else []
        if topic and topic not in queue:
            if len(queue)>=4:raise ValueError('Idle queue holds at most four topics')
            queue.append(topic)
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('idle_queue',json.dumps(queue)))
        # A plain status read (every idle run calls configure() first) writes nothing: the database file is synced
        # between the PC and the laptop, and an unchanged row rewritten on both left a conflict copy each time.
        if enabled is not None:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('idle_enabled',json.dumps(enabled)))
        row=db.execute("SELECT value FROM settings WHERE key='idle_enabled'").fetchone()
    return {'enabled':bool(row and json.loads(row[0])), 'queued':queue,'daily_studies':2,
            'background_gpu_inference':False,'meaning':'Source references only; practice is a separate explicit operation'}


def once(*,gaming=None,study=None):
    with users.acting_as('owner'):
        state=configure()
        if not state['enabled'] or not state['queued'] or research.stopped():return {'state':'paused_or_empty'}
        if gaming is None:
            from tools.gaming import game_mode,running_game
            gaming=lambda:game_mode() or bool(running_game())
        if gaming():return {'state':'paused_for_game'}
        import psutil
        battery=psutil.sensors_battery()
        if battery is not None and not battery.power_plugged:return {'state':'paused_on_battery'}
        day=datetime.now(timezone.utc).date().isoformat()
        with research.database() as db:
            current=db.execute("SELECT value FROM settings WHERE key='idle_queue'").fetchone()
            queue=json.loads(current[0]) if current else []
            enabled=db.execute("SELECT value FROM settings WHERE key='idle_enabled'").fetchone()
            if not queue or not enabled or not json.loads(enabled[0]):return {'state':'paused_or_empty'}
            row=db.execute('SELECT value FROM settings WHERE key=?',('idle_count:'+day,)).fetchone()
            count=int(row[0]) if row else 0
            if count>=2:return {'state':'daily_limit'}
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('idle_count:'+day,str(count+1)))
            topic=queue[0]
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('idle_queue',json.dumps(queue[1:])))
        if study is None:
            from core.tutorial_learning import study
        result=study(topic)
        with research.database() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('idle_last',json.dumps({'at':time.time(),'topic':topic,'state':result['state']})))
        return result
