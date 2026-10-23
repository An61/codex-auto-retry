#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retry_watcher.py -- 监控容量提示，只点击播放图标按钮。

典型场景：Codex（CLI / IDE 插件 / 桌面 App）弹出
    "Selected model is at capacity. Please try a different model."
这里按按钮图标定义状态：播放按钮表示当前暂停、点击后会开始运行；
暂停按钮表示当前运行中、不能点击。容量提示触发后，脚本持续监控播放按钮，
播放按钮出现就点击；点击后按轮询间隔复查，暂停按钮(运行中)不会点击。

依赖:
    pip install pyautogui rapidocr-onnxruntime

用法:
    python retry_watcher.py                      默认: 每 2s 扫描并持续监控
    python retry_watcher.py --windows 2           左右两块区域独立监控
    python retry_watcher.py --window-region 0,0,1000,1152 --window-region 1000,0,1048,1152
    python retry_watcher.py --dry-run            只检测和打印, 不真正点击
    python retry_watcher.py --interval 1 --restore-delay 1
    python retry_watcher.py --region 100,200,900,600          只扫屏幕某区域(更快)
    python retry_watcher.py --no-restore                    兼容参数, 当前模式忽略
    pythonw retry_watcher.py --log retry.log                  无窗口后台运行并记日志(默认 5 MB 自动轮换)
    python retry_watcher.py --stable-frames 3                 连续 3 帧确认后才点

Codex 场景 (已适配, 也可直接双击 codex_retry.bat; BAT 默认监控左右两个窗口):
    python retry_watcher.py --trigger "at capacity"
    * 只在容量提示出现后寻找播放按钮(当前暂停状态);
    * 播放按钮模板不匹配暂停按钮(当前运行状态), 因此运行中不会点击;
    * 点击后继续扫描, 播放按钮仍存在且达到轮询间隔就再次点击;
    * 按钮切换为暂停按钮(运行中)时不会点击;
    * 终端里这句英文会被软换行拆成两行, 触发词支持跨行拼接匹配。

容量提示出现后，主流程优先点击播放按钮；如果当前未识别到播放按钮，
但 OCR 在容量提示同一行找到“重试”，则点击该重试按钮作为备用动作。

恢复目标模板:
    默认使用脚本目录下的 restore_target.png。模板是播放图标圆形按钮
    (对应当前暂停状态); pause_target.png 用于否决暂停图标匹配。需要替换模板时:
    python retry_watcher.py --restore-template restore_target.png

自检技巧: 先 --dry-run，确认日志出现"已检测到容量提示"和
"检测到播放按钮"，再去掉 --dry-run。

安全:
    * 把鼠标猛甩到屏幕左上角 -> 立即中止(failsafe)
    * Ctrl+C 停止
    * 必须先 OCR 到容量提示，再匹配播放图标模板才点击
    * 点击后持续监控，播放图标仍存在且达到间隔才再次点击
"""

import argparse
import os
import threading
import time
from datetime import datetime

import numpy as np
import pyautogui

pyautogui.FAILSAFE = True  # 鼠标移到左上角立即抛异常, 用于紧急停止

DEFAULT_TRIGGER = "Selected model is at capacity"
DEFAULT_BUTTON = "重试"
DEFAULT_RESTORE_TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "restore_target.png"
)
DEFAULT_PAUSE_TEMPLATE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "pause_target.png"
)

_LOG_FILE = None
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with _LOG_LOCK:
        print(line, flush=True)
        if _LOG_FILE:
            try:
                encoded = (line + "\n").encode("utf-8")
                if _LOG_MAX_BYTES > 0:
                    current_size = os.path.getsize(_LOG_FILE) if os.path.exists(_LOG_FILE) else 0
                    if current_size + len(encoded) > _LOG_MAX_BYTES:
                        backup = _LOG_FILE + ".1"
                        if os.path.exists(backup):
                            os.remove(backup)
                        if os.path.exists(_LOG_FILE):
                            os.replace(_LOG_FILE, backup)
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(encoded.decode("utf-8"))
            except OSError:
                pass


def load_ocr():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _remap_ocr_detections(detections, scale, pad_x, pad_y, width, height):
    """Map OCR boxes from a padded/scaled image back to screen coordinates."""
    mapped = []
    for det in detections or []:
        box, text, score = det[0], det[1], det[2]
        new_box = []
        for point in box:
            x = min(width - 1, max(0, (float(point[0]) - pad_x) / scale))
            y = min(height - 1, max(0, (float(point[1]) - pad_y) / scale))
            new_box.append([x, y])
        mapped.append([new_box, text, score])
    return mapped


def ocr_with_fallback(ocr, img, keywords=()):
    """Run normal OCR, then a padded high-contrast pass for dark compact UIs.

    The Codex alert can be only ~138 px tall and uses white text on a near-black
    surface. RapidOCR may return no boxes on that raw frame. A padded binary
    pass improves detector recall, and all coordinates are mapped back before
    the caller performs any click calculation.
    """
    result, _ = ocr(img)
    detections = result or []
    normalized_keywords = [norm(k) for k in keywords if norm(k)]
    has_keyword = any(
        any(k in norm(str(det[1])) for k in normalized_keywords)
        for det in detections
    )
    if detections and (not normalized_keywords or has_keyword):
        return detections

    try:
        import cv2
    except ImportError:
        return detections
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # White text on a black surface becomes black text on a white surface.
    binary = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)[1]
    pad_x = pad_y = 40
    scale = 3.0
    binary = cv2.copyMakeBorder(
        binary, pad_y, pad_y, pad_x, pad_x, cv2.BORDER_CONSTANT, value=255
    )
    binary = cv2.resize(
        binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
    )
    binary = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
    fallback, _ = ocr(np.ascontiguousarray(binary))
    if not fallback:
        return detections
    return _remap_ocr_detections(
        fallback, scale, pad_x * scale, pad_y * scale,
        img.shape[1], img.shape[0],
    )


def norm(s: str) -> str:
    """归一化: 去空白、转小写, 容忍 OCR 多空格/大小写差异"""
    return "".join(str(s).split()).lower()


def find_hits(detections, keywords, min_score):
    """在 OCR 结果中找包含 keywords 的文本行, 返回 [{text, score, x, y, x1, y1, x2, y2}]"""
    kws = [norm(k) for k in keywords if norm(k)]
    hits = []
    for det in detections or []:
        box, text, score = det[0], det[1], float(det[2])
        if score < min_score:
            continue
        if any(k in norm(text) for k in kws):
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            hits.append(
                {
                    "text": text,
                    "score": round(score, 2),
                    "exact": norm(text) in kws,
                    "x": (min(xs) + max(xs)) / 2.0,
                    "y": (min(ys) + max(ys)) / 2.0,
                    "x1": min(xs),
                    "y1": min(ys),
                    "x2": max(xs),
                    "y2": max(ys),
                }
            )
    return hits


def trigger_anchors(detections, triggers, min_score):
    """Return direct trigger hits plus useful anchors for OCR-wrapped text.

    A terminal may split ``at capacity`` across two OCR boxes.  The joined
    text check still confirms the trigger, but it leaves button selection
    without a location.  Matching meaningful trigger words gives us a safe
    local anchor for the geometry scorer.
    """
    direct = find_hits(detections, triggers, min_score)
    if direct:
        return direct

    tokens = {
        norm(token)
        for trigger in triggers
        for token in str(trigger).split()
        if len(norm(token)) >= 3
    }
    if not tokens:
        return []
    anchors = []
    for det in detections or []:
        box, text, score = det[0], str(det[1]), float(det[2])
        if score < min_score or not any(token in norm(text) for token in tokens):
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        anchors.append(
            {
                "text": text,
                "score": round(score, 2),
                "exact": False,
                "x": (min(xs) + max(xs)) / 2.0,
                "y": (min(ys) + max(ys)) / 2.0,
                "x1": min(xs),
                "y1": min(ys),
                "x2": max(xs),
                "y2": max(ys),
            }
        )
    return anchors


def joined_text(detections):
    """把全屏 OCR 文本按阅读顺序(先上下后左右)拼成一整段。

    终端里一句长英文会被软换行拆成多个文本区域, 逐行子串匹配会失败;
    拼接后即可整体匹配(两边都做了去空白归一化)。
    """
    items = []
    for det in detections or []:
        box = det[0]
        top = min(float(p[1]) for p in box)
        left = min(float(p[0]) for p in box)
        items.append((top, left, str(det[1])))
    items.sort(key=lambda t: (t[0], t[1]))
    return norm("".join(t[2] for t in items))


def order_by_distance(btn_hits, msg_hits):
    """按离提示文字的远近排序按钮候选(近的优先), 没有提示位置时保持原序"""
    if msg_hits:
        m = msg_hits[0]
        return sorted(
            btn_hits,
            key=lambda b: (b["x"] - m["x"]) ** 2 + (b["y"] - m["y"]) ** 2,
        )
    return list(btn_hits)


def choose_button(btn_hits, msg_hits, side="right"):
    """Choose the button candidate using text quality and UI geometry.

    Nearest-distance alone can choose a stale or unrelated ``重试`` label.
    Prefer an exact button OCR hit that sits on the expected side of the
    trigger and shares its row; keep a distance fallback for wrapped layouts.
    """
    if not btn_hits:
        return None
    if not msg_hits:
        return max(btn_hits, key=lambda b: (b.get("exact", False), b["score"]))

    def score(button):
        best = -10_000.0
        for msg in msg_hits:
            mh = max(1.0, msg["y2"] - msg["y1"])
            bh = max(1.0, button["y2"] - button["y1"])
            vertical_overlap = max(
                0.0, min(msg["y2"], button["y2"]) - max(msg["y1"], button["y1"])
            )
            same_row = (
                vertical_overlap >= 0.25 * min(mh, bh)
                or abs(button["y"] - msg["y"]) <= 1.5 * max(mh, bh)
            )
            dx = button["x"] - msg["x"]
            dy = abs(button["y"] - msg["y"])
            distance = (dx * dx + dy * dy) ** 0.5
            value = button["score"] * 2.0 + (2.0 if button.get("exact") else 0.0)
            if same_row:
                expected = dx >= 0 if side == "right" else dx <= 0
                value += 8.0 if expected else -8.0
                value += max(0.0, 2.0 - min(distance, 400.0) / 200.0)
            else:
                value += max(0.0, 1.0 - min(distance, 600.0) / 600.0)
            best = max(best, value)
        return best

    return max(btn_hits, key=score)


def choose_retry_button(detections, msg_hits, button=DEFAULT_BUTTON,
                        min_score=0.5, side="right"):
    """Choose a retry text hit only when it is anchored to the capacity message.

    Requiring a message anchor prevents an unrelated ``重试`` elsewhere in the
    desktop from becoming a click target.  The caller may refine a merged OCR
    box with :func:`refine_button` before clicking.
    """
    if not msg_hits:
        return None
    button_hits = find_hits(detections, [button], min_score)
    return choose_button(button_hits, msg_hits, side) if button_hits else None


def locate_retry_button(ocr, img, detections, msg_hits, button=DEFAULT_BUTTON,
                        min_score=0.5, region=None, side="right"):
    """Locate a retry text button near the capacity message.

    Returns a screen-coordinate target or ``None``.  If OCR merged the full
    sentence and button into one box, crop and re-run OCR on the expected side
    so the click lands on the button label instead of the sentence center.
    """
    hit = choose_retry_button(detections, msg_hits, button, min_score, side)
    if hit is None:
        return None
    kw_norms = [norm(button)]
    point = (hit["x"], hit["y"])
    if len(norm(str(hit.get("text", "")))) > len(kw_norms[0]) + 2:
        refined = refine_button(ocr, img, hit, kw_norms, min_score, side)
        if refined is not None:
            point = refined
    ox = region[0] if region else 0
    oy = region[1] if region else 0
    return {
        "x": point[0] + ox,
        "y": point[1] + oy,
        "text": hit.get("text", ""),
        "score": hit.get("score", 0.0),
    }


def update_stable_retry_target(previous, stable_count, current,
                               stable_frames=2, stable_distance=45.0):
    """Return updated retry state and a target only after stable confirmation."""
    if current is None:
        return None, 0, None
    if previous is None:
        count = 1
    else:
        distance = (
            (current["x"] - previous["x"]) ** 2
            + (current["y"] - previous["y"]) ** 2
        ) ** 0.5
        count = stable_count + 1 if distance <= stable_distance else 1
    target = current if count >= max(1, stable_frames) else None
    return current, count, target


def visual_button_center(img, msg_hits, side="right"):
    """Find a likely filled accent-button center near the trigger.

    This is a fallback for low-quality OCR.  It intentionally requires a
    reasonably large, saturated rectangular component so ordinary text is
    not treated as a clickable control.
    """
    if not msg_hits:
        return None
    try:
        import cv2
    except ImportError:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([85, 80, 70], dtype=np.uint8),
        np.array([140, 255, 255], dtype=np.uint8),
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < 500 or h < 18 or w < 45 or w / max(h, 1) < 1.2 or w / max(h, 1) > 8:
            continue
        cx, cy = x + w / 2.0, y + h / 2.0
        for msg in msg_hits:
            mh = max(1.0, msg["y2"] - msg["y1"])
            row_distance = abs(cy - msg["y"])
            expected = cx >= msg["x"] if side == "right" else cx <= msg["x"]
            if expected and row_distance <= max(90.0, 4.0 * mh):
                candidates.append((row_distance, cx, cy))
    if not candidates:
        return None
    _, cx, cy = min(candidates)
    return cx, cy


def refine_button(ocr, img, hit, kw_norms, min_score, side="right"):
    """按钮文字和提示文字被 OCR 合并进同一文本框时, 裁出该侧重新 OCR 精修位置。

    img 为 BGR ndarray(与 OCR 输入一致), hit 为 find_hits 的返回项。
    返回精修后的 (x, y)(相对 img 的坐标), 失败返回 None。
    """
    x1, y1, x2, y2 = hit["x1"], hit["y1"], hit["x2"], hit["y2"]
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    pad_y = int(h * 0.4)
    cy1 = max(0, int(y1 - pad_y))
    cy2 = min(img.shape[0], int(y2 + pad_y))
    # Try narrow-to-wide crops.  The narrow crop isolates a short button label;
    # wider crops keep a fallback when the UI has a large gap or OCR padding.
    widths = [max(3.0 * h, 70.0), max(5.0 * h, 110.0), max(w * 0.35, 140.0)]
    best = None
    for crop_w in widths:
        if side == "right":
            cx1 = max(0, int(x2 - crop_w))
            cx2 = min(img.shape[1], int(x2))
        else:
            cx1 = max(0, int(x1))
            cx2 = min(img.shape[1], int(x1 + crop_w))
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        crop = np.ascontiguousarray(img[cy1:cy2, cx1:cx2])
        if crop.size == 0:
            continue
        result, _ = ocr(crop)
        for det in result or []:
            box, text, score = det[0], det[1], float(det[2])
            if score < min_score or not any(k in norm(text) for k in kw_norms):
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            bx1, bx2, by = min(xs), max(xs), (min(ys) + max(ys)) / 2.0
            exact = norm(text) in kw_norms
            candidate = (
                0 if exact else 1,
                -bx2 if side == "right" else bx1,
                bx1,
                bx2,
                by,
                norm(text),
                cx1,
                cy1,
            )
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        if best is not None and best[0] == 0:
            break
    if best is None:
        return None
    _, _, bx1, bx2, by, text, base_x, base_y = best
    max_kw = max((len(k) for k in kw_norms), default=0)
    if len(text) <= max_kw + 1:
        cx = (bx1 + bx2) / 2.0  # 裁剪后是干净的按钮文本, 取精确中心
    else:
        # 裁剪后仍合并: 按钮在该侧边缘, 按侧边估算中心
        if side == "right":
            cx = bx2 - min(50.0, (bx2 - bx1) * 0.35)
        else:
            cx = bx1 + min(50.0, (bx2 - bx1) * 0.35)
    return (base_x + cx, base_y + by)


def to_bgr(pil_image):
    """PIL RGB/RGBA/grayscale -> OpenCV BGR contiguous array."""
    pixels = np.asarray(pil_image)
    if pixels.ndim == 2:
        pixels = np.repeat(pixels[:, :, None], 3, axis=2)
    elif pixels.shape[2] == 4:
        pixels = pixels[:, :, :3]
    return np.ascontiguousarray(pixels[:, :, ::-1])


def parse_region(s):
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("格式应为 x,y,w,h")
    return tuple(int(p) for p in parts)


def locate_stable_template(
    template,
    confidence,
    region,
    interval,
    timeout,
    stable_frames,
    stable_distance,
    glyph_confidence=0.65,
    pause_template=None,
    pause_glyph_confidence=0.55,
    pause_score_margin=0.05,
):
    """Find a play icon repeatedly until its position and glyph are stable."""
    if not template or not os.path.exists(template):
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    previous = None
    stable_count = 0
    while time.monotonic() <= deadline:
        try:
            shot = pyautogui.screenshot(region=region)
        except pyautogui.FailSafeException:
            raise
        except Exception as exc:
            log(f"播放按钮截图失败: {exc}")
            return None
        target = locate_template_in_image(
            template, shot, confidence, region, glyph_confidence,
            pause_template, pause_glyph_confidence, pause_score_margin,
        )
        if target is None:
            previous = None
            stable_count = 0
            time.sleep(min(max(interval, 0.1), 1.0))
            continue
        if previous is not None:
            distance = (
                (target["x"] - previous["x"]) ** 2
                + (target["y"] - previous["y"]) ** 2
            ) ** 0.5
            stable_count = stable_count + 1 if distance <= stable_distance else 1
        else:
            stable_count = 1
        previous = target
        if stable_count >= max(1, stable_frames):
            return target
        time.sleep(min(max(interval, 0.1), 1.0))
    return None


def play_glyph_score(template, shot, loc):
    """Score the inner play glyph, excluding the shared circular background."""
    try:
        import cv2
        template_gray = cv2.imread(template, cv2.IMREAD_GRAYSCALE)
        if template_gray is None:
            return None
        screen_gray = np.asarray(shot.convert("L"), dtype=np.uint8)
        x1, y1 = int(loc.left), int(loc.top)
        x2, y2 = x1 + template_gray.shape[1], y1 + template_gray.shape[0]
        if x1 < 0 or y1 < 0 or y2 > screen_gray.shape[0] or x2 > screen_gray.shape[1]:
            return None
        patch = screen_gray[y1:y2, x1:x2]
        height, width = template_gray.shape[:2]
        # Keep the comparison inside the glyph; the outer ring is shared by
        # play and pause states and must not decide the click.
        half = max(4, min(6, min(height, width) // 4))
        cx, cy = width // 2, height // 2
        tx1, tx2 = cx - half, cx + half + 1
        ty1, ty2 = cy - half, cy + half + 1
        template_roi = template_gray[ty1:ty2, tx1:tx2].astype(np.float32)
        patch_roi = patch[ty1:ty2, tx1:tx2].astype(np.float32)
        score = cv2.matchTemplate(
            patch_roi, template_roi, cv2.TM_CCOEFF_NORMED
        )
        return float(score[0, 0])
    except (ImportError, OSError, ValueError):
        return None
    except Exception:
        return None


def locate_template_in_image(
    template,
    shot,
    confidence,
    region=None,
    glyph_confidence=0.65,
    pause_template=None,
    pause_glyph_confidence=0.55,
    pause_score_margin=0.05,
):
    """Locate the highest-scoring play glyph and reject pause-glyph matches."""
    if not template or not os.path.exists(template):
        return None
    try:
        matches = list(pyautogui.locateAll(template, shot, confidence=confidence))
    except Exception as exc:
        if exc.__class__.__name__ == "ImageNotFoundException":
            return None
        log(f"播放按钮状态检测失败: {exc}")
        return None
    best = None
    for loc in matches or ():
        glyph_score = play_glyph_score(template, shot, loc)
        if glyph_score is None or glyph_score < glyph_confidence:
            continue
        pause_score = None
        if pause_template and os.path.exists(pause_template):
            pause_score = play_glyph_score(pause_template, shot, loc)
            if (
                pause_score is not None
                and pause_score >= pause_glyph_confidence
                and pause_score >= glyph_score - pause_score_margin
            ):
                continue
        if best is None or glyph_score > best[0]:
            best = (glyph_score, pause_score, loc)
    if best is None:
        return None
    glyph_score, pause_score, loc = best
    ox = region[0] if region else 0
    oy = region[1] if region else 0
    return {
        "x": loc.left + loc.width / 2.0 + ox,
        "y": loc.top + loc.height / 2.0 + oy,
        "glyph_score": glyph_score,
        "pause_score": pause_score,
    }


def move_pointer_away(target, region=None):
    """Move to the corner farthest from the clicked control in its region."""
    screen_w, screen_h = pyautogui.size()
    if region:
        x, y, width, height = region
    else:
        x, y, width, height = 0, 0, screen_w, screen_h
    margin = 20
    corners = (
        (x + margin, y + margin),
        (x + max(margin, width - margin), y + margin),
        (x + margin, y + max(margin, height - margin)),
        (x + max(margin, width - margin), y + max(margin, height - margin)),
    )
    safe_x, safe_y = max(
        corners,
        key=lambda point: (point[0] - target["x"]) ** 2 + (point[1] - target["y"]) ** 2,
    )
    pyautogui.moveTo(
        min(max(1, safe_x), max(1, screen_w - 1)),
        min(max(1, safe_y), max(1, screen_h - 1)),
        duration=0,
    )


def click_play_button(
    template,
    confidence,
    region,
    delay,
    interval,
    timeout,
    stable_frames,
    stable_distance,
    glyph_confidence=0.65,
    pause_template=None,
    pause_glyph_confidence=0.55,
    pause_score_margin=0.05,
    dry_run=False,
):
    """Wait for and click the play-icon control from a template."""
    if not template:
        return None
    time.sleep(max(0.0, delay))
    target = locate_stable_template(
        template, confidence, region, interval, timeout,
        stable_frames, stable_distance, glyph_confidence,
        pause_template, pause_glyph_confidence, pause_score_margin,
    )
    if target is None:
        log(f"未找到播放按钮: {template}")
        return None
    if not dry_run:
        pyautogui.click(target["x"], target["y"])
        move_pointer_away(target, region)
    return target


def split_monitor_regions(region, count):
    """Split a screen or supplied region into equal vertical monitor areas."""
    if count <= 1:
        return [region]
    if region is None:
        screen_w, screen_h = pyautogui.size()
        x, y, width, height = 0, 0, screen_w, screen_h
    else:
        x, y, width, height = region
    regions = []
    for index in range(count):
        left = x + (width * index) // count
        right = x + (width * (index + 1)) // count
        regions.append((left, y, max(1, right - left), height))
    return regions


def monitor_region(region, args, triggers, trigger_norms, window_index, stop_event):
    """Monitor one independent window region until the shared stop event is set."""
    prefix = f"窗口{window_index}"

    def wlog(message):
        log(f"{prefix} | {message}")

    try:
        wlog("加载 OCR 模型(首次需要几秒)...")
        ocr = load_ocr()
    except Exception as exc:
        wlog(f"OCR 初始化失败: {exc}")
        stop_event.set()
        return

    wlog(
        f"OCR 就绪 | 监控区域: {region or '全屏'} | 容量提示: {triggers} | "
        f"播放模板: {args.restore_template} | 暂停负模板: {args.pause_template}"
    )
    if args.dry_run:
        wlog("*** dry-run 模式: 只报告, 不点击 ***")

    clicks = 0
    capacity_seen = False
    last_click_time = 0.0
    previous_retry_target = None
    retry_stable_count = 0
    scan_count = 0
    last_heartbeat = time.monotonic()

    while not stop_event.is_set():
        scan_count += 1
        try:
            shot = pyautogui.screenshot(region=region)
        except pyautogui.FailSafeException:
            wlog("failsafe 触发(鼠标在左上角), 已停止所有窗口监控")
            stop_event.set()
            return
        except Exception as exc:
            wlog(f"截图失败: {exc}")
            stop_event.wait(max(0.1, args.interval))
            continue

        try:
            img = to_bgr(shot)
            dets = ocr_with_fallback(ocr, img, triggers)
            direct_hits = find_hits(dets, triggers, args.min_score)
            joined = joined_text(dets)
            capacity_present = bool(direct_hits) or any(
                keyword in joined for keyword in trigger_norms
            )
        except Exception as exc:
            wlog(f"本轮 OCR 失败, 保持监控并重试: {exc}")
            stop_event.wait(max(0.1, args.interval))
            continue

        play_button_target = locate_template_in_image(
            args.restore_template, shot, args.restore_confidence, region,
            args.play_glyph_confidence,
            args.pause_template, args.pause_glyph_confidence,
            args.pause_score_margin,
        )
        retry_button_target = None
        if capacity_present and play_button_target is None:
            trigger_hits = direct_hits or trigger_anchors(
                dets, triggers, args.min_score
            )
            retry_button_target = locate_retry_button(
                ocr, img, dets, trigger_hits, args.button,
                args.min_score, region, args.button_side,
            )
        retry_click_target = None
        if retry_button_target is None or play_button_target is not None:
            previous_retry_target = None
            retry_stable_count = 0
        else:
            (
                previous_retry_target,
                retry_stable_count,
                retry_click_target,
            ) = update_stable_retry_target(
                previous_retry_target,
                retry_stable_count,
                retry_button_target,
                args.stable_frames,
                args.stable_distance,
            )

        now = time.monotonic()
        if now - last_heartbeat >= max(1.0, args.heartbeat):
            if capacity_seen and play_button_target is not None:
                cooldown_left = max(0.0, args.restore_transition_poll - (now - last_click_time))
                state = (
                    f"播放按钮(暂停状态)已出现, 将点击 | 冷却剩余 {cooldown_left:.0f}s"
                    if cooldown_left <= 0
                    else f"播放按钮(暂停状态)仍存在 | 冷却剩余 {cooldown_left:.0f}s"
                )
            elif capacity_present and retry_click_target is not None:
                state = "未检测到播放按钮, 容量提示旁重试按钮已稳定, 将点击重试"
            elif capacity_present and retry_button_target is not None:
                state = "未检测到播放按钮, 等待重试按钮连续确认"
            elif capacity_seen:
                state = "暂停按钮(运行中)或未检测到可点击按钮, 不点击"
            else:
                state = "等待容量提示"
            wlog(f"监控仍在运行 | 状态: {state} | 扫描: {scan_count}")
            last_heartbeat = now

        if capacity_present and not capacity_seen:
            capacity_seen = True
            wlog("已检测到容量提示, 开始持续监控播放按钮")

        if not capacity_seen or (play_button_target is None and retry_click_target is None):
            stop_event.wait(max(0.1, args.interval))
            continue

        cooldown = max(0.0, args.restore_transition_poll)
        elapsed = time.monotonic() - last_click_time
        if elapsed < cooldown:
            stop_event.wait(min(max(cooldown - elapsed, 0.1), max(args.interval, 0.1)))
            continue

        if play_button_target is not None:
            try:
                target = click_play_button(
                    args.restore_template,
                    args.restore_confidence,
                    region,
                    args.restore_delay,
                    args.interval,
                    args.restore_timeout,
                    args.stable_frames,
                    args.stable_distance,
                    args.play_glyph_confidence,
                    args.pause_template,
                    args.pause_glyph_confidence,
                    args.pause_score_margin,
                    dry_run=args.dry_run,
                )
            except pyautogui.FailSafeException:
                wlog("播放按钮触发 failsafe, 已停止所有窗口监控")
                stop_event.set()
                return
            except Exception as exc:
                wlog(f"播放按钮点击动作失败, 保持监控并重试: {exc}")
                stop_event.wait(max(0.1, args.interval))
                continue
            if target is None:
                stop_event.wait(max(0.1, args.interval))
                continue
            clicks += 1
            if args.dry_run:
                wlog(f"检测到播放按钮, dry-run 未点击: ({target['x']:.0f},{target['y']:.0f})")
            else:
                wlog(
                    f"检测到播放按钮, 已点击: ({target['x']:.0f},{target['y']:.0f}) "
                    f"| 图标分数={target.get('glyph_score', 0.0):.2f} -> 第 {clicks} 次点击"
                )
        else:
            try:
                if not args.dry_run:
                    pyautogui.click(retry_click_target["x"], retry_click_target["y"])
                    move_pointer_away(retry_click_target, region)
            except pyautogui.FailSafeException:
                wlog("重试按钮触发 failsafe, 已停止所有窗口监控")
                stop_event.set()
                return
            except Exception as exc:
                wlog(f"重试按钮点击动作失败, 保持监控并重试: {exc}")
                stop_event.wait(max(0.1, args.interval))
                continue
            clicks += 1
            if args.dry_run:
                wlog(
                    f"检测到重试按钮, dry-run 未点击: "
                    f"({retry_click_target['x']:.0f},{retry_click_target['y']:.0f})"
                )
            else:
                wlog(
                    f"未检测到播放按钮, 已点击容量提示旁重试按钮: "
                    f"({retry_click_target['x']:.0f},{retry_click_target['y']:.0f}) "
                    f"-> 第 {clicks} 次点击"
                )
        last_click_time = time.monotonic()
        stop_event.wait(min(max(args.restore_transition_poll, 0.1), max(args.interval, 0.1)))


def main() -> None:
    global _LOG_FILE, _LOG_MAX_BYTES

    ap = argparse.ArgumentParser(
        description="监控容量提示, 只点击播放图标按钮(当前暂停状态)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--interval", type=float, default=2.0, help="扫描间隔(秒), 默认 2")
    ap.add_argument("--cooldown", type=float, default=0.0,
                    help="兼容旧参数, 现在不会暂停监控")
    ap.add_argument("--region", type=parse_region, default=None, help="兼容参数: 监控区域 x,y,w,h")
    ap.add_argument("--window-region", dest="window_regions", type=parse_region,
                    action="append", help="指定一个窗口区域; 可重复传入多个")
    ap.add_argument("--windows", type=int, default=1,
                    help="没有显式区域时把屏幕左右分成几个窗口区域, 默认 1")
    ap.add_argument("--trigger", default=DEFAULT_TRIGGER, help="触发提示文字, 逗号分隔多个")
    ap.add_argument("--button", default=DEFAULT_BUTTON, help=argparse.SUPPRESS)
    ap.add_argument("--no-msg-check", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--min-score", type=float, default=0.5, help="OCR 置信度阈值")
    ap.add_argument("--template", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--confidence", type=float, default=0.85, help=argparse.SUPPRESS)
    ap.add_argument("--button-side", choices=["right", "left"], default="right",
                    help=argparse.SUPPRESS)
    ap.add_argument("--stable-frames", type=int, default=2,
                    help="连续命中多少帧才点击, 默认 2(降低误点)")
    ap.add_argument("--stable-distance", type=float, default=45.0,
                    help="连续帧坐标允许偏差(像素), 默认 45")
    ap.add_argument("--no-visual-fallback", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--restore-template", default=DEFAULT_RESTORE_TEMPLATE,
                    help="播放图标按钮模板(当前暂停状态), 默认使用脚本目录下 restore_target.png")
    ap.add_argument("--pause-template", default=DEFAULT_PAUSE_TEMPLATE,
                    help="暂停图标负匹配模板, 默认使用脚本目录下 pause_target.png")
    ap.add_argument("--no-restore", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--restore-delay", type=float, default=2.0,
                    help="容量提示出现后等待播放按钮的秒数, 默认 2")
    ap.add_argument("--restore-timeout", type=float, default=8.0,
                    help="播放按钮最多寻找多少秒, 默认 8")
    ap.add_argument("--restore-confidence", type=float, default=0.78,
                    help="播放按钮外框模板匹配置信度, 默认 0.78")
    ap.add_argument("--play-glyph-confidence", type=float, default=0.65,
                    help="播放图标内部形状置信度, 默认 0.65")
    ap.add_argument("--pause-glyph-confidence", type=float, default=0.55,
                    help="暂停图标负匹配置信度, 默认 0.55")
    ap.add_argument("--pause-score-margin", type=float, default=0.05,
                    help="播放/暂停图标分数允许差值, 默认 0.05")
    ap.add_argument("--restore-transition-poll", type=float, default=10.0,
                    help="点击后检查播放/暂停状态的间隔(秒), 默认 10")
    ap.add_argument("--heartbeat", type=float, default=10.0,
                    help="后台运行心跳日志间隔(秒), 默认 10")
    ap.add_argument("--dry-run", action="store_true", help="只检测和打印, 不真正点击")
    ap.add_argument("--log", default=None, help="同时把日志追加写入文件(配合 pythonw 用)")
    ap.add_argument("--log-max-mb", type=float, default=5.0,
                    help="日志文件上限(MB), 超过后轮换为 .1, 默认 5; 设为 0 表示不限")
    args = ap.parse_args()

    if args.windows < 1:
        ap.error("--windows 必须大于等于 1")
    _LOG_FILE = args.log
    _LOG_MAX_BYTES = max(0, int(args.log_max_mb * 1024 * 1024))
    triggers = [t.strip() for t in args.trigger.split(",") if t.strip()]
    trigger_norms = [norm(t) for t in triggers if norm(t)]
    if args.no_msg_check:
        log("忽略 --no-msg-check: 当前模式必须检测到容量提示才允许恢复目标")
    if args.cooldown:
        log("忽略 --cooldown: 当前模式持续监控, 点击后不主动暂停")
    if args.no_restore:
        log("忽略 --no-restore: 当前模式必须监控播放按钮")

    if args.window_regions:
        regions = args.window_regions
    elif args.region is not None:
        regions = [args.region]
    else:
        regions = split_monitor_regions(None, args.windows)
    log(
        f"持续监控启动 | 窗口数: {len(regions)} | 区域: {regions} | 间隔: {args.interval}s | "
        "播放按钮=暂停状态时点击, 暂停按钮=运行中时不点击"
    )
    if len(regions) > 1:
        log("多窗口模式: 每个区域独立检测容量提示、按钮状态和点击冷却")

    stop_event = threading.Event()
    threads = []
    for index, region in enumerate(regions, 1):
        thread = threading.Thread(
            target=monitor_region,
            args=(region, args, triggers, trigger_norms, index, stop_event),
            name=f"codex-auto-retry-window-{index}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.25)
    except KeyboardInterrupt:
        log("Ctrl+C, 正在停止所有窗口监控")
        stop_event.set()
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
