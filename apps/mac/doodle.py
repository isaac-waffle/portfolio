"""줄 노트 위에 볼펜으로 대충 그린 낙서 스타일의 앱 아이콘 (빨간 여백 선 없이 파란 줄만).

    .venv/bin/python apps/mac/doodle.py robot out.png     # 대상: SUBJECTS 참고
    .venv/bin/python apps/mac/doodle.py hub               # 허브 행 아이콘 전부 → apps/mac/hub_icons/*.png (작게 봐도 보이게 굵은 펜)
"""
from __future__ import annotations

import math
import sys

import numpy as np
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 2  # 슈퍼샘플링
W = 1024 * S
INK = (32, 48, 128)


class Pen:
    def __init__(self, seed: int, wmul: float = 1.0, zoom: float = 1.0):
        self.rng = np.random.default_rng(seed)
        self.wmul, self.zoom = wmul, zoom  # 작은 아이콘: 펜을 굵게, 그림을 크게
        self.layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.layer)

    def _wobble(self, pts, amp):
        pts = np.asarray(pts, float)
        seg = np.r_[0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
        n = max(int(seg[-1] / 5), 2)
        t = np.linspace(0, seg[-1], n)
        xy = np.c_[np.interp(t, seg, pts[:, 0]), np.interp(t, seg, pts[:, 1])]
        tang = np.gradient(xy, axis=0)
        norm = np.c_[-tang[:, 1], tang[:, 0]] / (np.hypot(*tang.T)[:, None] + 1e-9)
        u = t / max(seg[-1], 1)
        off = sum(self.rng.uniform(0.3, 1) * np.sin(2 * math.pi * f * u + self.rng.uniform(0, 6.3))
                  for f in self.rng.uniform(0.5, 3.5, 3)) / 3
        return xy + norm * (off * amp)[:, None]

    def line(self, pts, width=7, amp=6, passes=1):
        z = self.zoom
        pts = [((x - 512) * z + 512, (y - 512) * z + 512) for x, y in pts]
        width, amp = width * self.wmul, amp * (1 + (self.wmul - 1) * 0.35)
        for k in range(passes):
            xy = self._wobble([(x * S, y * S) for x, y in pts], amp * S) + self.rng.normal(0, 2 * S * k * self.wmul ** 0.5, 2)
            wv = width * S * (0.8 + 0.4 * self.rng.random(len(xy)))
            for i in range(len(xy) - 1):
                a = (220 if self.rng.random() > 0.04 else 120)  # 볼펜 잉크가 가끔 끊기는 느낌
                self.d.line([tuple(xy[i]), tuple(xy[i + 1])], fill=INK + (a,), width=int(wv[i]))
                r = wv[i] / 2
                self.d.ellipse([xy[i][0] - r, xy[i][1] - r, xy[i][0] + r, xy[i][1] + r], fill=INK + (a,))

    def ellipse(self, cx, cy, rx, ry, width=7, amp=5, over=0.25):
        start = self.rng.uniform(0, 2 * math.pi)
        th = np.linspace(start, start + 2 * math.pi * (1 + over), 60)  # 끝이 겹치게 한 바퀴 넘김
        self.line([(cx + rx * math.cos(a), cy + ry * math.sin(a)) for a in th], width, amp)

    def scribble(self, x0, y0, x1, y1, n=9, width=5):
        pts = []
        for i in range(n):
            y = y0 + (y1 - y0) * i / (n - 1)
            pts += [(x0 + self.rng.uniform(-6, 6), y), (x1 + self.rng.uniform(-6, 6), y + (y1 - y0) / (2 * n))]
        self.line(pts, width, 3)


def paper() -> tuple[Image.Image, Image.Image]:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sh = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([100 * S, 114 * S, 924 * S, 938 * S], radius=186 * S, fill=(0, 0, 0, 70))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(16 * S)))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle([100 * S, 100 * S, 924 * S, 924 * S], radius=186 * S, fill=255)
    p = Image.new("RGBA", (W, W), (252, 250, 241, 255))
    noise = np.random.default_rng(1).normal(0, 3, (W, W)).astype(np.int16)
    arr = np.asarray(p, np.int16).copy()
    arr[..., :3] = np.clip(arr[..., :3] + noise[..., None], 0, 255)
    p = Image.fromarray(arr.astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(p)
    for y in range(236, 924, 62):
        d.line([(100 * S, y * S), (924 * S, y * S)], fill=(150, 185, 225, 255), width=3 * S)
    img.paste(p, (0, 0), mask)
    return img, mask


def robot(p: Pen):
    p.line([(370, 330), (660, 322), (668, 560), (362, 566), (368, 326)], passes=2)        # 머리
    p.ellipse(450, 420, 34, 30)
    p.ellipse(580, 418, 30, 34)
    p.line([(440, 500), (600, 496)], amp=4)                                             # 입
    for x in (480, 520, 560):
        p.line([(x, 486), (x + 2, 512)], width=5, amp=2)
    p.line([(515, 322), (512, 250)])                                                    # 안테나
    p.ellipse(512, 234, 20, 18)
    p.line([(420, 566), (410, 760), (628, 768), (612, 560)])                            # 몸
    p.line([(362, 600), (300, 690)])
    p.line([(668, 600), (730, 680)])
    p.line([(290, 280), (760, 800)], width=11, amp=8, passes=2)                         # 크게 X
    p.line([(752, 270), (282, 812)], width=11, amp=8, passes=2)


def person(p: Pen):
    p.ellipse(512, 360, 92, 98)
    p.ellipse(480, 345, 8, 9, width=10)
    p.ellipse(546, 342, 8, 9, width=10)
    p.line([(468, 395), (500, 420), (540, 418), (566, 390)])
    p.line([(512, 458), (518, 680)], passes=2)
    p.line([(515, 520), (400, 600)])
    p.line([(515, 515), (640, 430), (690, 380)])
    for a in (-0.5, 0, 0.5):                                                            # 손 흔드는 표시
        p.line([(720 + 30 * math.cos(a - 0.6), 350 + 30 * math.sin(a - 0.6)),
                (760 + 45 * math.cos(a - 0.6), 330 + 45 * math.sin(a - 0.6))], width=5, amp=2)
    p.line([(518, 680), (440, 820)])
    p.line([(518, 680), (610, 815)])


def cat(p: Pen):
    p.ellipse(512, 470, 190, 160)
    p.line([(352, 390), (360, 250), (450, 330)])                                         # 귀
    p.line([(575, 330), (668, 245), (676, 392)])
    p.ellipse(440, 450, 22, 30, width=9)
    p.ellipse(588, 448, 22, 30, width=9)
    p.line([(500, 520), (526, 520), (512, 540), (500, 520)], width=6, amp=2)
    p.line([(512, 540), (490, 570)], width=5)
    p.line([(512, 540), (536, 568)], width=5)
    for dy in (-12, 14):
        p.line([(470, 530 + dy), (320, 515 + dy * 2)], width=4, amp=3)
        p.line([(556, 530 + dy), (705, 512 + dy * 2)], width=4, amp=3)
    p.line([(420, 628), (430, 760), (600, 765), (606, 626)])
    p.line([(600, 740), (720, 700), (750, 600)])                                         # 꼬리


def house(p: Pen):
    p.line([(330, 480), (512, 300), (700, 482)], passes=2)
    p.line([(370, 450), (372, 780), (660, 776), (658, 452)])
    p.line([(470, 776), (468, 640), (548, 642), (552, 774)])
    p.line([(580, 520), (640, 520), (642, 580), (582, 582), (580, 518)], width=6)
    p.scribble(586, 528, 634, 574, n=4, width=4)
    p.ellipse(760, 260, 50, 48)
    for a in np.linspace(0, 2 * math.pi, 9)[:-1]:
        p.line([(760 + 70 * math.cos(a), 260 + 70 * math.sin(a)), (760 + 105 * math.cos(a), 260 + 105 * math.sin(a))],
               width=5, amp=2)
    p.line([(560, 400), (560, 330), (600, 330), (600, 360)], width=6)                   # 굴뚝
    p.line([(300, 800), (740, 806)], width=6, amp=4)


def arc(cx, cy, r, a0, a1, n=24):
    return [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(a0, a1, n)]


def chart(p: Pen):
    p.line([(290, 250), (296, 770), (790, 764)])                                         # 축
    p.line([(330, 680), (430, 560), (505, 615), (610, 440), (720, 330)], passes=2)        # 오르는 선
    p.line([(650, 335), (722, 326), (708, 400)])                                         # 화살촉


def calendar(p: Pen):
    p.line([(285, 330), (742, 322), (748, 772), (280, 778), (286, 326)], passes=2)
    p.line([(286, 440), (744, 432)])
    for x in (400, 625):                                                                 # 고리
        p.line([(x, 270), (x + 4, 380)], width=9)
    for y in (560, 670):
        p.line([(300, y), (730, y - 6)], width=4, amp=3)
    for x in (430, 585):
        p.line([(x, 445), (x + 6, 770)], width=4, amp=3)
    p.ellipse(510, 615, 72, 50, width=8)                                                 # 표시한 날짜


def traders(p: Pen):
    for i, x in enumerate((300, 440, 580, 720)):                                         # 4명
        p.ellipse(x, 400, 50, 54)
        p.line([(x, 456), (x + 4, 620)])
        p.line([(x + 2, 690 - 160), (x - 55, 590)])
        p.line([(x + 4, 620), (x - 40, 740)])
        p.line([(x + 4, 620), (x + 48, 735)])
        if i < 3:
            p.line([(x + 2, 520), (x + 55, 585)])
    p.line([(725, 520), (790, 440), (800, 290)])                                         # 돌격대장 깃발
    p.line([(800, 290), (880, 315), (802, 345)], width=6)


def crystal(p: Pen):
    p.ellipse(512, 440, 200, 190)
    p.ellipse(512, 440, 196, 188, amp=7)
    p.line([(400, 340), (430, 300), (480, 285)], width=6)                                # 반짝임
    p.line([(345, 640), (680, 636), (720, 750), (305, 756), (346, 636)])                 # 받침
    for cx, cy, r in ((560, 470, 34), (460, 520, 22)):
        p.line([(cx - r, cy), (cx + r, cy)], width=5, amp=2)
        p.line([(cx, cy - r), (cx, cy + r)], width=5, amp=2)


def globe(p: Pen):
    p.ellipse(512, 512, 240, 236)
    p.ellipse(512, 512, 105, 236, width=6)
    p.line([(275, 512), (750, 506)], width=6)
    p.line(arc(512, 900, 470, -2.07, -1.07), width=5)
    p.line(arc(512, 124, 470, 1.07, 2.07), width=5)


def camera(p: Pen):
    p.line([(270, 410), (760, 402), (768, 742), (262, 748), (272, 406)], passes=2)
    p.line([(410, 405), (450, 330), (590, 326), (628, 402)])
    p.ellipse(515, 575, 115, 112)
    p.ellipse(515, 575, 50, 48, width=6)
    p.line([(660, 450), (720, 448), (722, 490), (662, 492), (660, 448)], width=5)


def flask(p: Pen):
    p.line([(420, 270), (604, 266)])
    p.line([(462, 272), (460, 455), (320, 750), (705, 752), (565, 455), (563, 268)], passes=2)
    p.line([(370, 640), (655, 636)], width=6)
    p.scribble(372, 652, 655, 735, n=5, width=5)
    p.ellipse(500, 560, 18, 18, width=5)
    p.ellipse(565, 505, 13, 13, width=5)


def moneybag(p: Pen):
    p.ellipse(512, 590, 210, 180)
    p.line([(435, 420), (395, 320), (465, 355), (512, 310), (558, 355), (630, 320), (590, 420)])
    p.line([(430, 425), (595, 418)], width=9)
    p.line([(565, 525), (520, 505), (470, 522), (472, 565), (515, 585), (560, 605), (558, 655), (505, 672), (455, 650)], width=9)
    p.line([(514, 470), (512, 705)], width=7)


def rocket(p: Pen):
    p.line([(512, 220), (606, 380), (604, 640), (420, 644), (418, 378), (512, 220)], passes=2)
    p.ellipse(512, 440, 44, 42)
    p.line([(420, 540), (330, 690), (420, 650)])
    p.line([(604, 540), (694, 690), (604, 650)])
    p.line([(450, 660), (470, 790), (512, 720), (550, 805), (575, 660)], width=6)


def cycle(p: Pen):
    p.line(arc(512, 512, 215, math.radians(200), math.radians(350)), passes=2)
    p.line([(690, 330), (725, 445), (630, 420)])
    p.line(arc(512, 512, 215, math.radians(20), math.radians(170)), passes=2)
    p.line([(335, 690), (300, 580), (395, 605)])


def branch(p: Pen):
    p.ellipse(370, 290, 52, 50)
    p.ellipse(372, 735, 52, 50)
    p.ellipse(660, 400, 52, 50)
    p.line([(370, 342), (372, 683)], passes=2)
    p.line([(660, 452), (650, 560), (540, 610), (385, 660)])


SUBJECTS = {"robot": (robot, 11), "person": (person, 5), "cat": (cat, 3), "house": (house, 8), "chart": (chart, 21),
            "calendar": (calendar, 22), "traders": (traders, 23), "crystal": (crystal, 24), "globe": (globe, 25),
            "camera": (camera, 26), "flask": (flask, 27), "moneybag": (moneybag, 28), "rocket": (rocket, 29),
            "cycle": (cycle, 30), "branch": (branch, 31)}
HUB = ["chart", "calendar", "traders", "crystal", "globe", "camera", "person", "flask", "moneybag", "house", "rocket",
       "cycle", "branch"]


def small_paper() -> Image.Image:
    """작은 아이콘용: 여백 없이 꽉 찬 줄 노트, 줄은 굵고 성기게."""
    p = Image.new("RGBA", (W, W), (252, 250, 241, 255))
    noise = np.random.default_rng(2).normal(0, 3, (W, W)).astype(np.int16)
    arr = np.asarray(p, np.int16).copy()
    arr[..., :3] = np.clip(arr[..., :3] + noise[..., None], 0, 255)
    p = Image.fromarray(arr.astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(p)
    for y in range(150, W // S, 150):
        d.line([(0, y * S), (W, y * S)], fill=(160, 192, 228, 255), width=9 * S)
    return p


def render_small(subject: str, out: Path, size: int = 128):
    fn, seed = SUBJECTS[subject]
    img = small_paper()
    pen = Pen(seed, wmul=4.2, zoom=1.22)
    fn(pen)
    img.alpha_composite(pen.layer.filter(ImageFilter.GaussianBlur(1.0 * S)))
    img.resize((size, size), Image.LANCZOS).save(out)


def render(subject: str, out: str):
    fn, seed = SUBJECTS[subject]
    img, mask = paper()
    pen = Pen(seed)
    fn(pen)
    ink = pen.layer.filter(ImageFilter.GaussianBlur(0.6 * S))
    clipped = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    clipped.paste(ink, (0, 0), Image.fromarray(np.minimum(np.asarray(mask), np.asarray(ink)[..., 3]).astype(np.uint8)))
    img.alpha_composite(clipped)
    img.resize((1024, 1024), Image.LANCZOS).save(out)


if __name__ == "__main__":
    if sys.argv[1] == "hub":
        d = Path(__file__).with_name("hub_icons")
        d.mkdir(exist_ok=True)
        for k in HUB:
            render_small(k, d / f"{k}.png")
            print(d / f"{k}.png")
    else:
        render(sys.argv[1], sys.argv[2])
