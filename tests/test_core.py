import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from core import CAL_POINTS,CHECK_POINTS,fit,predict,validate,head_ok,stable_median,save_profile,load_profile


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.size=np.array([1280,800])
        self.iris=lambda p:np.array([p[0]*.14-.07,p[1]*.055-.08])
        self.samples=[self.iris(p) for p in CAL_POINTS]
        self.targets=[np.array(p)*self.size for p in CAL_POINTS]
        self.profile=fit(self.samples,self.targets)

    def test_independent_points_recover_coordinates(self):
        metrics=validate(self.profile,[self.iris(p) for p in CHECK_POINTS],
                         [np.array(p)*self.size for p in CHECK_POINTS],self.size)
        self.assertTrue(metrics['passed'])
        self.assertLess(metrics['mean_px'],1.)

    def test_bad_validation_does_not_pass(self):
        metrics=validate(self.profile,[self.iris(p)+[.05,.03] for p in CHECK_POINTS],
                         [np.array(p)*self.size for p in CHECK_POINTS],self.size)
        self.assertFalse(metrics['passed'])
        self.assertGreater(metrics['mean_px'],300.)

    def test_flat_and_missing_data_rejected(self):
        with self.assertRaises(ValueError):fit([[0,0]]*16,self.targets)
        with self.assertRaises(ValueError):fit(self.samples[:5],self.targets[:5])

    def test_outliers_and_motion(self):
        rng=np.random.default_rng(7)
        samples=rng.normal([.02,-.04],.002,(30,2)).tolist()+[[.8,.7]]*3
        self.assertTrue(np.allclose(stable_median(samples),[.02,-.04],atol=.002))
        with self.assertRaises(ValueError):stable_median([[0,0]]*4)
        self.assertTrue(head_ok([0,.4,.5,.5,.2],[0,.4,.5,.5,.2]))
        self.assertFalse(head_ok([.1,.4,.5,.5,.2],[0,.4,.5,.5,.2]))
        self.assertFalse(head_ok([0,.4,.5,.5,.3],[0,.4,.5,.5,.2]))

    def test_profile_camera_and_window_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.json';geo={'width':1280,'height':800,'window':[10,10,1280,828]}
            p=dict(self.profile,schema=1,camera_id='test',geometry=geo,head_reference=[0,.4,.5,.5,.2],validation={'passed':True})
            save_profile(path,p)
            self.assertIsNotNone(load_profile(path,'test',geo))
            self.assertIsNone(load_profile(path,'other',geo))
            self.assertIsNone(load_profile(path,'test',dict(geo,width=1400)))
            p['validation']['passed']=False;save_profile(path,p)
            self.assertIsNone(load_profile(path,'test',geo))
            path.write_text('broken')
            self.assertIsNone(load_profile(path,'test',geo))

if __name__=='__main__':unittest.main()
