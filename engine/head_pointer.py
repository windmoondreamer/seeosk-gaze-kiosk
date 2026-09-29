"""Head-only pointer: short neutral capture, no gaze fitting or iris input."""
import math
import statistics


# macOS 손쉬운 사용의 머리 포인터도 같은 0~1 눈금을 쓰며, 기본값이 0.5입니다.
# 0.5에서 기존 배율이 그대로 나오도록 두 배로 환산합니다.
DEFAULT_SENSITIVITY = .25
BASE_GAIN_X, BASE_GAIN_Y = 4.5, 5.5


class HeadPointer:
    def __init__(self, sensitivity=DEFAULT_SENSITIVITY):
        self.sensitivity = sensitivity
        self.reset()

    @property
    def sensitivity(self):
        return self._sensitivity

    @sensitivity.setter
    def sensitivity(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(value):
            self._sensitivity = max(.05, min(1., value))

    def reset(self):
        self.reference = None
        self.samples = []
        self.started = None
        self.last = None
        self.xy = None

    def update(self, x, y, now, width, height):
        if not all(math.isfinite(v) for v in (x, y, now, width, height)) or min(width, height) <= 0:
            return None
        if self.last is not None and now-self.last > .4 and self.reference is None:
            self.samples = []
            self.started = None
        dt = min(.1, max(.001, now-self.last)) if self.last is not None else 1/30
        self.last = now
        if self.reference is None:
            if self.started is None:
                self.started = now
            self.samples.append((x, y))
            self.samples = self.samples[-40:]
            if now-self.started < .7 or len(self.samples) < 12:
                return None
            xs, ys = zip(*self.samples)
            if max(xs)-min(xs) > .025 or max(ys)-min(ys) > .025:
                self.samples = [(x,y)]
                self.started = now
                return None
            self.reference = (statistics.median(xs), statistics.median(ys))
        # Ratios use eye corners and nose, independent of camera resolution.
        gain = 2*self._sensitivity
        target = [max(0., min(width-1., width*(.5+(x-self.reference[0])*BASE_GAIN_X*gain))),
                  max(0., min(height-1., height*(.5+(y-self.reference[1])*BASE_GAIN_Y*gain)))]
        if self.xy is None:
            self.xy = target
        else:
            distance = math.hypot(target[0]-self.xy[0], target[1]-self.xy[1])
            # Fast for intentional movement, steady for holding small targets.
            alpha = 1-math.exp(-dt*(9 if distance < 25 else 28))
            if distance > 2:
                self.xy = [a+alpha*(b-a) for a,b in zip(self.xy,target)]
        return tuple(self.xy)
