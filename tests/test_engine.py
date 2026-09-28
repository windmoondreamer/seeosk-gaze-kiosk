import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import worker
from core import CAL_POINTS, CHECK_POINTS


class EngineFlowTests(unittest.TestCase):
    def test_calibration_collect_validate_save_cancel_and_invalidate(self):
        events=[]
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker,'DATA',Path(tmp)), \
             patch.object(worker,'Detector'), patch.object(worker,'emit',lambda kind,**data:events.append(dict(type=kind,**data))):
            e=worker.Engine();e.cap=object();e.camera={'id':'fake-camera'}
            e.geometry={'width':1280,'height':800,'window':[0,0,1280,828]}
            e.handle({'cmd':'calibrate','generation':1})
            head=[0.,.4,.5,.5,.2]
            for phase,points in [('calibrate',CAL_POINTS),('check',CHECK_POINTS)]:
                for i,(x,y) in enumerate(points):
                    e.handle({'cmd':'collect','generation':1,'phase':phase,'index':i})
                    t=time.monotonic()
                    for frame in range(15):
                        e.sample([x*.14-.07,y*.055-.08],head,t+frame*.1,True)
            result=next(m for m in events if m['type']=='calibration_result')
            self.assertTrue(result['validation']['passed'])
            self.assertLess(result['validation']['mean_px'],1.)
            self.assertTrue(e.profile_path().is_file())
            old=e.profile
            e.handle({'cmd':'calibrate','generation':2})
            e.handle({'cmd':'collect','generation':1,'phase':'calibrate','index':0})
            self.assertIsNone(e.collection, 'stale generation must be ignored')
            e.handle({'cmd':'cancel'})
            self.assertIs(e.profile,old)
            e.handle({'cmd':'configure','geometry':{'width':1400,'height':800}})
            self.assertIsNone(e.profile)

    def test_low_accuracy_keeps_pointer_profile(self):
        from core import fit, load_profile
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker,'DATA',Path(tmp)), patch.object(worker,'Detector'), patch.object(worker,'emit'):
            e=worker.Engine();e.cap=object();e.camera={'id':'fake'}
            e.geometry={'width':1280,'height':800}
            e.handle({'cmd':'calibrate','generation':1})
            head=[0.,.4,.5,.5,.2]
            for phase,points in [('calibrate',CAL_POINTS),('check',CHECK_POINTS)]:
                for i,(x,y) in enumerate(points):
                    e.handle({'cmd':'collect','generation':1,'phase':phase,'index':i})
                    t=time.monotonic()
                    offset=.04 if phase=='check' else 0
                    for frame in range(15):
                        e.sample([x*.14-.07+offset,y*.055-.08],head,t+frame*.1,True)
            self.assertFalse(e.profile['validation']['passed'])
            self.assertTrue(e.profile['validation']['pointer_usable'])
            self.assertIsNotNone(load_profile(e.profile_path(),'fake',e.geometry))

    def test_offscreen_gaze_moves_to_edge_instead_of_freezing(self):
        features=dict(has_iris=True,face_w=200,gaze_x=0,gaze_y=0,
                      head_x=0,head_y=.4,pos_x=.5,pos_y=.5)
        with patch.object(worker,'Detector'), patch.object(worker,'emit') as emit,              patch.object(worker,'extract_features',return_value=features),              patch.object(worker,'predict',return_value=np.array([400.,-500.])):
            e=worker.Engine();e.cap=Mock();e.cap.read.return_value=(True,np.zeros((480,640,3),dtype=np.uint8))
            e.tracker=Mock();e.tracker.process.return_value=(object(),{})
            e.geometry={'width':1000,'height':800};e.profile={}
            e.last_preview=time.monotonic()
            e.frame()
            event=emit.call_args.kwargs
            self.assertTrue(event['valid'])
            self.assertEqual(event['y'],0.)
            self.assertEqual(event['x'],400.)

    def test_missing_face_never_advances_collection(self):
        with patch.object(worker,'Detector'),patch.object(worker,'emit'):
            e=worker.Engine();e.cap=object();e.camera={'id':'fake-camera'}
            e.geometry={'width':1280,'height':800}
            e.handle({'cmd':'calibrate','generation':1})
            e.handle({'cmd':'collect','generation':1,'phase':'calibrate','index':0})
            t=time.monotonic()
            for frame in range(200):e.sample(None,None,t+frame*.03,False)
            self.assertEqual(e.samples,[])
            self.assertEqual(e.collection['elapsed'],0.)

if __name__=='__main__':unittest.main()
