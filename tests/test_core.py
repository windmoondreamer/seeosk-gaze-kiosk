import json
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from core import CAL_POINTS,CHECK_POINTS,fit,predict,validate,head_ok,stable_median,stable_summary,save_profile,load_profile,fit_gaze_zones,classify_gaze_zone


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.size=np.array([1280,800])
        self.iris=lambda p:np.array([p[0]*.14-.07,p[1]*.055-.08])
        self.samples=[self.iris(p) for p in CAL_POINTS]
        self.targets=[np.array(p) for p in CAL_POINTS]
        self.profile=fit(self.samples,self.targets)

    def test_independent_points_recover_coordinates(self):
        metrics=validate(self.profile,[self.iris(p) for p in CHECK_POINTS],
                         [np.array(p) for p in CHECK_POINTS],self.size)
        self.assertTrue(metrics['passed'])
        self.assertLess(metrics['mean_px'],1.)

    def test_bad_validation_does_not_pass(self):
        metrics=validate(self.profile,[self.iris(p)+[.05,.03] for p in CHECK_POINTS],
                         [np.array(p) for p in CHECK_POINTS],self.size)
        self.assertFalse(metrics['passed'])
        self.assertGreater(metrics['mean_px'],300.)

    def test_discrete_gaze_zones_follow_calibration_targets(self):
        zones=fit_gaze_zones(self.samples)
        self.assertIsNotNone(zones)
        for i,sample in enumerate(self.samples):
            self.assertEqual(classify_gaze_zone(zones,sample),i)
        self.assertIsNotNone(fit_gaze_zones(self.samples,np.full((9,2),.002)))
        self.assertIsNone(fit_gaze_zones(self.samples,np.full((9,2),.02)), 'direction gap must exceed measured jitter')
        self.assertIsNone(fit_gaze_zones(self.samples,[[.01,.01]]))
        self.assertIsNone(fit_gaze_zones([[.1,.1]]*9))
        self.assertIsNone(classify_gaze_zone(None,[0,0]))

    def test_flat_and_missing_data_rejected(self):
        with self.assertRaises(ValueError):fit([[0,0]]*16,self.targets)
        with self.assertRaises(ValueError):fit(self.samples[:5],self.targets[:5])

    def test_outliers_and_motion(self):
        rng=np.random.default_rng(7)
        samples=rng.normal([.02,-.04],.002,(30,2)).tolist()+[[.8,.7]]*3
        self.assertTrue(np.allclose(stable_median(samples),[.02,-.04],atol=.002))
        median,spread=stable_summary(samples)
        self.assertEqual(len(median),2);self.assertEqual(len(spread),2)
        with self.assertRaises(ValueError):stable_median([[0,0]]*4)
        self.assertTrue(head_ok([0,.4,.5,.5,.2],[0,.4,.5,.5,.2]))
        self.assertFalse(head_ok([.1,.4,.5,.5,.2],[0,.4,.5,.5,.2]))
        self.assertFalse(head_ok([0,.4,.5,.5,.3],[0,.4,.5,.5,.2]))

    def test_normalized_profile_survives_window_resize_but_requires_same_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'p.json';geo={'width':1280,'height':800,'window':[10,10,1280,828]}
            p=dict(self.profile,schema=4,gaze_zones=fit_gaze_zones(self.samples),camera_id='test',geometry={'coordinate_space':'normalized'},head_reference=[0,.4,.5,.5,.2],validation={'passed':True})
            save_profile(path,p)
            self.assertIsNotNone(load_profile(path,'test',geo))
            self.assertIsNone(load_profile(path,'other',geo))
            resized=dict(geo,width=1400,height=900,window=[100,50,1400,928])
            self.assertIsNotNone(load_profile(path,'test',resized))
            p['schema']=3;save_profile(path,p)
            self.assertIsNone(load_profile(path,'test',geo),'pixel-space legacy profiles need recalibration')
            p['schema']=4
            p['validation']['passed']=False;save_profile(path,p)
            self.assertIsNone(load_profile(path,'test',geo))
            path.write_text('broken')
            self.assertIsNone(load_profile(path,'test',geo))

if __name__=='__main__':unittest.main()
