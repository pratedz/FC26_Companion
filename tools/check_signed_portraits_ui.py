"""Check the actual 240px Club detail card, invisibly and without game writes."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk
from PIL import Image
from companion.app import events as E
from companion.app.commands import signed_portraits as portraits
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.ids import new_ulid
from companion.domain.outcome import JobResult
from companion.ui.surfaces.club import _paint_selection_card


def main():
    with tempfile.TemporaryDirectory(prefix='companion-portrait-ui-') as tmp:
        paths = TempAppPaths(Path(tmp))
        transport = FakeTransport()
        transport.set_liveness(replace(transport.liveness(), save_uid='SAVE', core_version='2.6.31'))
        db = Database(paths.state_db)
        svc = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=db)
        svc.store.dispatch(E.SquadSynced(save_uid='SAVE', session_id=transport.liveness().session_id,
            teamid=241, players=[{'playerid':73669, 'name':'Maradona'}], taken=svc.clock.now()))
        jid = new_ulid()
        job = {'job_id':jid, 'requires':{'save_uid':'SAVE'}, 'ops':[{'op':'add_to_team','id':'add','teamid':241,
            'batch':[{'dummy_pool':[73669], 'player':{'source_playerid':190045,'names':{'commonname':'Maradona'}}}]}]}
        wire = {'job_id':jid, 'state':'done','ok':True,'env':{'save_uid':'SAVE'},'ops':[{'op':'add_to_team','id':'add','ok':True,
            'counts':{'fields_written':10},'data':{'teamid':241,'verified':True,'playerids':[73669]}}]}
        db.record_submitted(job_id=jid, job=job)
        db.record_finished(jid, outcome=JobResult.from_wire(wire).outcome, result=wire, save_uid='SAVE')
        base = portraits._source_dir(svc, 190045)
        base.mkdir(parents=True)
        Image.new('RGBA',(180,180),(40,100,200,150)).save(base/'preview.png')
        Image.new('RGBA',(180,180),(40,100,200,150)).save(base/'portrait.dds', pixel_format='DXT5')
        portraits._json(base/'state.json', {'state':'ready','source_playerid':190045,'sha256':portraits._hash(base/'portrait.dds')})
        root = ctk.CTk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes('-alpha',0)
        root.attributes('-disabled',True)
        root.geometry('240x650+30000+30000')
        errors = []
        root.report_callback_exception = lambda kind,value,trace: errors.append(str(value))
        try:
            body = ctk.CTkFrame(root, width=240, height=650)
            body.pack(fill='both',expand=True)
            body.pack_propagate(False)
            _paint_selection_card(body, {'id':73669,'name':'Maradona','pos':'CAM','ovr':95,'pot':95,'age':25},svc)
            root.deiconify()
            root.update()
            root.update_idletasks()
            labels, images, buttons = [], [], []
            def walk(widget):
                if isinstance(widget, ctk.CTkLabel):
                    labels.append(widget.cget('text'))
                    if widget.cget('image'):
                        images.append(widget)
                if isinstance(widget, ctk.CTkButton):
                    buttons.append(widget.cget('text'))
                    assert widget.winfo_width() > 30
                    assert widget.winfo_x()+widget.winfo_width() <= widget.master.winfo_width()+1
                    assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()
                for child in widget.winfo_children():
                    walk(child)
            walk(body)
            assert len(images)==1 and 'Maradona' in labels
            assert 'Install portrait in game' in buttons and 'Restore previous portrait' in buttons
            assert not errors, errors
            print('Hidden 240px Club detail check passed: signed portrait, name, optional install/restore controls fit.')
        finally:
            root.destroy()
            db.close()


if __name__=='__main__':
    main()
