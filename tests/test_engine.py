import sys
import unittest
import time
from pathlib import Path
from unittest.mock import patch, Mock
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine'))
import worker
from head_pointer import HeadPointer


class HeadControlTests(unittest.TestCase):
    def ready(self, sensitivity=.5):
        p = HeadPointer(sensitivity)
        for i in range(25):
            p.update(0, .4, i/30, 1000, 800)
        self.assertIsNotNone(p.reference)
        return p

    def settle(self, p, x, y, frames=40):
        for _ in range(frames):
            result = p.update(x, y, p.last+1/30, 1000, 800)
        return result

    def test_short_neutral_then_reaches_all_edges(self):
        # 0.5는 macOS 머리 포인터와 같은 눈금의 기준점입니다.
        p = self.ready()
        for x,y,expected in [(-.2,.4,(0,400)),(.2,.4,(999,400)),(0,.2,(500,0)),(0,.6,(500,799))]:
            result = self.settle(p, x, y)
            self.assertLess(np.linalg.norm(np.array(result)-expected),3)

    def test_default_sensitivity_matches_the_macos_head_pointer_default(self):
        self.assertAlmostEqual(HeadPointer().sensitivity, .25)

    def test_lower_sensitivity_moves_the_pointer_a_shorter_distance(self):
        slow = self.settle(self.ready(.25), .05, .4)
        fast = self.settle(self.ready(.5), .05, .4)
        slow_travel, fast_travel = slow[0]-500, fast[0]-500
        self.assertGreater(slow_travel, 0)
        # 0.25는 0.5의 절반 배율이므로 같은 고개 움직임에서 이동량도 절반입니다.
        self.assertAlmostEqual(slow_travel/fast_travel, .5, places=2)

    def test_sensitivity_is_kept_when_the_pointer_recenters(self):
        p = self.ready(.8)
        p.reset()
        self.assertAlmostEqual(p.sensitivity, .8)

    def test_moving_start_does_not_capture_bad_center(self):
        p=HeadPointer()
        for i in range(50):p.update((i%2)*.1,.4,i/30,1000,800)
        self.assertIsNone(p.reference)

    def test_recenter_and_missing_frames_restart_neutral_capture(self):
        p=self.ready();p.reset()
        self.assertIsNone(p.update(0,.4,0,1000,800))
        self.assertIsNone(p.update(0,.4,2,1000,800))
        self.assertIsNone(p.reference)

    def test_no_iris_or_blink_required_for_head_movement(self):
        features=dict(has_iris=False,face_w=200,head_x=.15,head_y=.4)
        with patch.object(worker,'Detector'),patch.object(worker,'emit') as emit,patch.object(worker,'extract_features',return_value=features) as extract:
            e=worker.Engine();e.cap=Mock();e.cap.read.return_value=(True,np.zeros((480,640,3),dtype=np.uint8))
            e.tracker=Mock();e.tracker.process.return_value=(object(),{'eyeBlinkLeft':1})
            e.geometry={'width':1000,'height':800};e.head_pointer=self.ready();e.head_pointer.last=time.monotonic()-.03
            e.frame()
            event=emit.call_args.kwargs
            self.assertTrue(event['valid']);self.assertGreater(event['x'],500)
            self.assertFalse(extract.call_args.kwargs['include_iris'])
            e.handle({'cmd':'configure','geometry':{'width':1200,'height':900}})
            self.assertIsNotNone(e.head_pointer.reference)
            e.handle({'cmd':'recenter'})
            self.assertIsNone(e.head_pointer.reference)

    def mouth_engine(self, clock):
        e = worker.Engine()
        e.cap = Mock(); e.cap.read.return_value = (True, np.zeros((480,640,3), dtype=np.uint8))
        e.tracker = Mock()
        e.geometry = {'width':1000,'height':800}
        e.head_pointer = self.ready(); e.head_pointer.last = clock.now
        e.last_frame = clock.now
        return e

    class Clock:
        """frame()이 부르는 단조 시계. 프레임마다 고정 간격으로 흐릅니다."""
        def __init__(self, step=.04):
            self.now, self.step = 1000.0, step

        def __call__(self):
            self.now += self.step
            return self.now

    def run_mouth(self, e, jaw, frames, emit):
        """입벌림 값을 고정한 채 프레임을 돌리고 그 사이 발생한 gesture_click 수를 셉니다."""
        e.tracker.process.return_value = (object(), {'jawOpen': jaw})
        before = sum(1 for c in emit.call_args_list if c.args and c.args[0] == 'gesture_action')
        for _ in range(frames):
            e.frame()
        after = sum(1 for c in emit.call_args_list if c.args and c.args[0] == 'gesture_action')
        return after-before

    def mouth_context(self):
        features = dict(has_iris=False, face_w=200, head_x=.15, head_y=.4)
        clock = self.Clock()
        return clock, patch.object(worker,'Detector'), patch.object(worker,'emit'), \
            patch.object(worker,'extract_features',return_value=features), \
            patch.object(worker.time,'monotonic',side_effect=clock)

    def test_mouth_open_fires_a_click_only_after_the_baseline_is_measured(self):
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            # 기준선은 3초(0.04초 프레임으로 75장)간 다문 입을 재며, 그동안은 발사하지 않습니다.
            self.assertEqual(self.run_mouth(e, .02, 60, emit), 0)
            self.assertLess(clock.now-1000, 3.0)
            self.assertEqual(self.run_mouth(e, .02, 30, emit), 0)
            self.assertGreaterEqual(self.run_mouth(e, .9, 10, emit), 1)

    def test_mouth_held_open_through_the_baseline_recovers_after_recenter(self):
        # 기준선을 입 벌린 채로 재면 그 값이 평상시로 학습되어 클릭이 막힙니다.
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            self.run_mouth(e, .9, 90, emit)
            self.assertEqual(self.run_mouth(e, .9, 10, emit), 0)
            e.handle({'cmd':'recenter'})
            e.head_pointer = self.ready(); e.head_pointer.last = clock.now
            self.run_mouth(e, .02, 90, emit)
            self.assertGreaterEqual(self.run_mouth(e, .9, 10, emit), 1)

    def test_mouth_click_carries_the_pointer_position(self):
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            self.run_mouth(e, .02, 90, emit)
            self.run_mouth(e, .9, 10, emit)
            clicks = [c for c in emit.call_args_list if c.args and c.args[0] == 'gesture_action']
            self.assertTrue(clicks)
            self.assertEqual(clicks[-1].kwargs['gesture'], 'jawopen')
            self.assertGreater(clicks[-1].kwargs['x'], 500)

    def test_holding_the_mouth_open_does_not_repeat_within_the_refractory_window(self):
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            self.run_mouth(e, .02, 90, emit)
            # 불응기 0.9초, 프레임 0.04초 → 20프레임 동안은 한 번만 인정됩니다.
            self.assertEqual(self.run_mouth(e, .9, 20, emit), 1)

    def test_tracking_reports_mouth_value_for_the_meter(self):
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            self.run_mouth(e, .37, 2, emit)
            tracking = [c for c in emit.call_args_list if c.args and c.args[0] == 'tracking']
            level = tracking[-1].kwargs['gestures']['left']
            self.assertAlmostEqual(level['value'], .37, places=3)
            self.assertEqual(level['gesture'], 'jawopen')
            self.assertIn('state', level)

    def test_higher_sensitivity_needs_a_smaller_expression(self):
        low = worker.make_detector('jawopen', 0.)
        default = worker.make_detector('jawopen')
        high = worker.make_detector('jawopen', 1.)
        self.assertGreater(low.min_margin, default.min_margin)
        self.assertGreater(default.min_margin, high.min_margin)
        # 기본값은 원래 임계값의 절반 아래여야 "조금만 벌려도" 눌립니다.
        self.assertLess(default.min_margin, low.min_margin/2)
        self.assertGreaterEqual(high.min_margin, .03)
        self.assertLess(high.k_on, low.k_on)

    def test_hard_to_reach_expressions_were_brought_down(self):
        # 미소와 윙크는 원래 기준이 높아 크게 지어야 넘었습니다.
        for name, ceiling in (('smile', .10), ('winkleft', .13), ('winkright', .13)):
            d = worker.make_detector(name)
            threshold = .02+max(d.k_on*.01, d.min_margin)
            self.assertLess(threshold, ceiling, name)

    def test_every_expression_reports_a_live_level(self):
        blend = {'jawOpen':.4,'eyeBlinkLeft':.8,'eyeBlinkRight':.1,
                 'mouthSmileLeft':.3,'mouthSmileRight':.5}
        levels = worker.expression_levels(blend)
        self.assertEqual(set(levels), set(worker.BLEND_GESTURES))
        self.assertAlmostEqual(levels['jawopen'], .4)
        self.assertAlmostEqual(levels['smile'], .4)          # 양쪽 평균
        self.assertAlmostEqual(levels['winkleft'], .7)       # 좌우 차이
        self.assertAlmostEqual(levels['winkright'], 0.)      # 음수는 0으로
        self.assertAlmostEqual(levels['longblink'], .1)      # 두 눈 중 작은 값

    def test_sensitivity_command_rebuilds_the_detectors(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit') as emit:
            e = worker.Engine()
            before = e.detectors['left'].min_margin
            e.handle({'cmd':'gesture_sensitivity','value':.0})
            self.assertGreater(e.detectors['left'].min_margin, before)
            self.assertAlmostEqual(emit.call_args.kwargs['value'], .0)
            self.assertIn('left', emit.call_args.kwargs['thresholds'])

    def test_a_broken_sensitivity_keeps_the_previous_one(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit'):
            e = worker.Engine()
            e.handle({'cmd':'gesture_sensitivity','value':.2})
            e.handle({'cmd':'gesture_sensitivity','value':'nope'})
            self.assertAlmostEqual(e.gesture_sensitivity, .2)
            e.handle({'cmd':'gesture_sensitivity','value':9})
            self.assertAlmostEqual(e.gesture_sensitivity, 1.)

    def test_a_smaller_opening_fires_at_the_default_sensitivity(self):
        # 예전 임계값(0.32) 아래인 0.22 정도만 벌려도 눌려야 합니다.
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            self.run_mouth(e, .02, 90, emit)
            self.assertGreaterEqual(self.run_mouth(e, .22, 10, emit), 1)

    def test_gesture_map_builds_one_detector_per_action(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit') as emit:
            e = worker.Engine()
            e.handle({'cmd':'gestures','map':{'left':'winkright','right':'browup','double':'nod'}})
            self.assertEqual(set(e.detectors), {'left','right','double'})
            self.assertEqual(e.gestures['left'], 'winkright')
            self.assertIsInstance(e.detectors['double'], worker.NodDetector)
            self.assertIsInstance(e.detectors['left'], worker.ThresholdDetector)
            self.assertEqual(emit.call_args.kwargs['map'], e.gestures)
            self.assertIn('longblink', emit.call_args.kwargs['available'])

    def test_unassigned_and_unknown_gestures_are_dropped(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit'):
            e = worker.Engine()
            e.handle({'cmd':'gestures','map':{'left':'none','right':'nosuchface','drag':'smile'}})
            self.assertEqual(set(e.detectors), {'drag'})

    def test_every_documented_expression_can_be_assigned(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit'):
            e = worker.Engine()
            for name in list(worker.BLEND_GESTURES)+['nod']:
                e.handle({'cmd':'gestures','map':{'left':name}})
                self.assertEqual(set(e.detectors), {'left'}, name)

    def test_each_action_fires_from_its_own_expression(self):
        clock, detector, emitter, extract, monotonic = self.mouth_context()
        with detector, emitter as emit, extract, monotonic:
            e = self.mouth_engine(clock)
            e.handle({'cmd':'gestures','map':{'left':'jawopen','right':'cheekPuff'.lower()}})
            e.tracker.process.return_value=(object(), {'jawOpen':.02,'cheekPuff':.02})
            for _ in range(90): e.frame()          # 기준선
            fired=lambda: [c.kwargs['action'] for c in emit.call_args_list
                           if c.args and c.args[0]=='gesture_action']
            before=len(fired())
            e.tracker.process.return_value=(object(), {'jawOpen':.9,'cheekPuff':.02})
            for _ in range(10): e.frame()
            self.assertEqual(fired()[before:], ['left'])
            before=len(fired())
            e.tracker.process.return_value=(object(), {'jawOpen':.02,'cheekPuff':.9})
            # 불응기 0.9초, 프레임 0.04초 -> 20프레임 안에서는 한 번만 인정됩니다.
            for _ in range(20): e.frame()
            self.assertEqual(fired()[before:], ['right'])

    def test_sensitivity_command_clamps_and_reports(self):
        with patch.object(worker,'Detector'), patch.object(worker,'emit') as emit:
            e = worker.Engine()
            e.handle({'cmd':'sensitivity','value':.4})
            self.assertAlmostEqual(e.head_pointer.sensitivity, .4)
            self.assertAlmostEqual(emit.call_args.kwargs['value'], .4)
            e.handle({'cmd':'sensitivity','value':5})
            self.assertAlmostEqual(e.head_pointer.sensitivity, 1.)

    def test_nan_never_moves_pointer(self):
        p=self.ready()
        self.assertIsNone(p.update(float('nan'),.4,2,1000,800))

if __name__=='__main__':unittest.main()
