# -*- coding: utf-8 -*-
"""retry_watcher 检测逻辑离线自检(不截图、不点击)。

用例1  单行提示 + 重试按钮 + 干扰"取消"        -> 命中且不误点
用例2  提示被换行拆断(终端软换行)              -> 跨行拼接匹配兜底
用例3  用户实测布局: 提示与"重试"同一行, 按钮在最右
       -> 端到端验证最终落点接近按钮真实中心(不管 OCR 是否合并)
用例4  单元级: 手工构造"整行合并框"            -> 直接验证 refine_button 精修
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)

import retry_watcher as rw  # noqa: E402

font_path = next(
    (p for p in (
        r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\Deng.ttf",
    ) if os.path.exists(p)),
    None,
)
assert font_path, "找不到中文字体"
font = ImageFont.truetype(font_path, 26)
scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))

os.makedirs(os.path.join(WORKSPACE, "test_assets"), exist_ok=True)
ocr = rw.load_ocr()

TRIGGER = rw.DEFAULT_TRIGGER
BUTTON = rw.DEFAULT_BUTTON
KW = rw.norm(BUTTON)
MSG = "Selected model is at capacity. Please try a different model."


def run_case(name, draw_fn, width=980, height=320):
    img = Image.new("RGB", (width, height), "white")
    draw_fn(ImageDraw.Draw(img))
    path = os.path.join(WORKSPACE, "test_assets", f"auto_retry_{name}.png")
    img.save(path)
    dets = ocr(rw.to_bgr(Image.open(path)))[0] or []
    print(f"\n=== 用例 {name} ===")
    for box, text, score in dets:
        print(f"  {float(score):.2f}  {text}")
    return path, dets


def btn_center_x(msg_text, gap=28):
    """提示文字左缘 x=30, 间隔 gap 后画按钮, 返回按钮文字真实中心 x"""
    return 30 + scratch.textlength(msg_text, font=font) + gap \
        + scratch.textlength(BUTTON, font=font) / 2


# ---- 用例 1: 单行提示 + 重试 + 干扰按钮取消 ----
def case_single(d):
    d.text((30, 30), MSG, fill="black", font=font)
    d.rounded_rectangle([430, 90, 530, 140], radius=8, outline="#999999")
    d.text((458, 100), "取消", fill="#333333", font=font)
    d.rounded_rectangle([600, 90, 700, 140], radius=8, fill="#1a73e8")
    d.text((628, 100), BUTTON, fill="white", font=font)


_, dets1 = run_case("single", case_single)
msgs1 = rw.find_hits(dets1, [TRIGGER], 0.5)
btns1 = rw.find_hits(dets1, [BUTTON], 0.5)
assert msgs1, "FAIL[1]: 没识别到提示文字"
assert btns1, "FAIL[1]: 没识别到重试按钮"
assert not any("取消" in b["text"] for b in btns1), "FAIL[1]: 误把取消当成了目标"
hit1 = rw.order_by_distance(btns1, msgs1)[0]
print(f"将点击: ({hit1['x']:.0f},{hit1['y']:.0f})  按钮真实中心=(654.5,119.0)")
assert abs(hit1["x"] - 654.5) < 40 and abs(hit1["y"] - 119.0) < 25, "FAIL[1]: 落点偏离"
chosen1 = rw.choose_button(btns1, msgs1)
assert chosen1 and abs(chosen1["x"] - 654.5) < 40, "FAIL[1]: 几何评分选错按钮"
retry1 = rw.choose_retry_button(dets1, msgs1)
assert retry1 and abs(retry1["x"] - 654.5) < 40, "FAIL[1]: 重试备用定位错误"
assert rw.choose_retry_button(dets1, []) is None, "FAIL[1]: 无容量提示锚点不应选择重试"


# ---- 用例 2: 提示被换行拆成两行(断点落在触发词中间) ----
def case_wrapped(d):
    d.text((30, 30), "Selected model is at", fill="black", font=font)
    d.text((30, 64), "capacity. Please try a different model.", fill="black", font=font)
    d.rounded_rectangle([600, 120, 700, 170], radius=8, fill="#1a73e8")
    d.text((628, 130), BUTTON, fill="white", font=font)


_, dets2 = run_case("wrapped", case_wrapped)
joined2 = rw.joined_text(dets2)
line_hits2 = rw.find_hits(dets2, [TRIGGER], 0.5)
btns2 = rw.find_hits(dets2, [BUTTON], 0.5)
print(f"拼接后全文: {joined2}")
print(f"逐行命中: {line_hits2} (预期为空, 靠拼接匹配兜底)")
assert not line_hits2, "FAIL[2]: 换行后逐行竟仍命中, 用例失去意义"
assert rw.norm(TRIGGER) in joined2, "FAIL[2]: 跨行拼接匹配没生效"
assert btns2, "FAIL[2]: 没识别到重试按钮"
anchors2 = rw.trigger_anchors(dets2, [TRIGGER], 0.5)
assert anchors2, "FAIL[2]: 换行提示没有生成位置锚点"
retry2 = rw.choose_retry_button(dets2, anchors2)
assert retry2, "FAIL[2]: 换行提示旁的重试按钮没有定位"
prev, count, click = rw.update_stable_retry_target(None, 0, retry2)
assert click is None and count == 1, "FAIL[2]: 重试按钮单帧不应点击"
prev, count, click = rw.update_stable_retry_target(prev, count, retry2)
assert click is retry2 and count == 2, "FAIL[2]: 重试按钮连续两帧后应允许点击"
assert rw.split_monitor_regions((0, 0, 10, 4), 2) == [
    (0, 0, 5, 4), (5, 0, 5, 4)
], "FAIL[2]: 左右窗口区域切分错误"


# ---- 用例 3: 用户实测布局——提示与重试同一行, 按钮在最右 ----
def case_user_layout(d):
    d.text((30, 60), MSG, fill="black", font=font)
    btn_x = 30 + scratch.textlength(MSG, font=font) + 28
    d.text((btn_x, 60), BUTTON, fill="#1a73e8", font=font)


_, dets3 = run_case("user_layout", case_user_layout)
true_cx3 = btn_center_x(MSG)
true_cy3 = 60 + 19.5  # 26px 字体 OCR 框中心的经验偏移(见用例1实测)
msgs3 = rw.find_hits(dets3, [TRIGGER], 0.5)
btns3 = rw.find_hits(dets3, [BUTTON], 0.5)
assert msgs3, "FAIL[3]: 没识别到提示文字"
assert btns3, "FAIL[3]: 没识别到重试按钮"
hit3 = rw.order_by_distance(btns3, msgs3)[0]
merged3 = len(rw.norm(hit3["text"])) > len(KW) + 2
print(f"按钮命中文本: {hit3['text']!r}  merged={merged3}")
tx, ty = hit3["x"], hit3["y"]
if merged3:
    print(f"合并框中心=({tx:.0f},{ty:.0f})  <- 旧行为会点这里(句子中间)")
    assert tx < true_cx3 - 120, "FAIL[3]: 未复现'合并导致中心点偏'的 bug"
    img3 = rw.to_bgr(Image.open(
        os.path.join(WORKSPACE, "test_assets", "auto_retry_user_layout.png")))
    r3 = rw.refine_button(ocr, img3, hit3, [KW], 0.5, "right")
    assert r3, "FAIL[3]: 合并框精修失败"
    tx, ty = r3
print(f"最终落点: ({tx:.0f},{ty:.0f})  真实按钮中心≈({true_cx3:.0f},{true_cy3:.0f})")
assert abs(tx - true_cx3) < 45, "FAIL[3]: 落点偏离按钮"
assert abs(ty - true_cy3) < 35, "FAIL[3]: 落点纵向偏离"


# ---- 用例 3b: 视觉兜底应锁定蓝色按钮矩形中心, 不受文字框偏移影响 ----
img3 = rw.to_bgr(Image.open(os.path.join(WORKSPACE, "test_assets", "auto_retry_screen.png")))
visual3 = rw.visual_button_center(img3, msgs1)
assert visual3, "FAIL[3b]: 蓝色按钮视觉兜底没有找到按钮"
assert abs(visual3[0] - 650) < 35 and abs(visual3[1] - 116) < 25, "FAIL[3b]: 视觉按钮中心偏离"


# ---- 用例 4: 单元级——手工构造整行合并框, 直接测 refine_button ----
def case_merge_unit(d):
    # 一整行连续字符串: 英文提示 + 重试, OCR 必然合并成一个框
    d.text((30, 60), MSG + " 重试", fill="black", font=font)


path4, dets4 = run_case("merge_unit", case_merge_unit)
true_cx4 = 30 + scratch.textlength(MSG + " ", font=font) \
    + scratch.textlength(BUTTON, font=font) / 2
# 用全部检测结果的外括框, 构造一个"整行被合并"的 hit
all_pts = [p for det in dets4 for p in det[0]]
manual = {
    "text": MSG + " " + BUTTON,
    "x1": min(float(p[0]) for p in all_pts), "y1": min(float(p[1]) for p in all_pts),
    "x2": max(float(p[0]) for p in all_pts), "y2": max(float(p[1]) for p in all_pts),
    "score": 0.9,
}
manual["x"] = (manual["x1"] + manual["x2"]) / 2.0
manual["y"] = (manual["y1"] + manual["y2"]) / 2.0
print(f"手工合并框: x1={manual['x1']:.0f} x2={manual['x2']:.0f} "
      f"中心=({manual['x']:.0f},{manual['y']:.0f})")
assert manual["x"] < true_cx4 - 120, "FAIL[4]: 合并框中心应远偏左"
img4 = rw.to_bgr(Image.open(path4))
r4 = rw.refine_button(ocr, img4, manual, [KW], 0.5, "right")
assert r4, "FAIL[4]: refine_button 返回 None"
print(f"精修落点: ({r4[0]:.0f},{r4[1]:.0f})  真实按钮中心≈({true_cx4:.0f},{true_cy3:.0f})")
assert abs(r4[0] - true_cx4) < 50, "FAIL[4]: 精修位置横向偏离按钮"
assert abs(r4[1] - true_cy3) < 35, "FAIL[4]: 精修位置纵向偏离"

print("\nSELFTEST OK (四个用例全部通过)")
