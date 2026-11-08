import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import traceback
import os
import shutil
import sys

from fontTools.ttLib import TTFont, TTCollection

# ==================== 答题卡版式常量（gen_sheet 与阅卷共用同一份真源） ====================
# 以下常量必须同时约束"生成答题卡"和"阅卷定位选项"，任何修改都会让两者的坐标保持一致。
SHEET_W, SHEET_H = 900, 1200      # 标准答题卡尺寸
FRAME_MARGIN = 20                 # 外框距图纸边缘的内边距
FRAME_THICKNESS = 5               # 外框线宽
OPT_BOX_W, OPT_BOX_H = 55, 42     # 单个选项框尺寸
OPT_FIRST_Y = 120                 # 第一题选项框顶部的 y
OPT_LINE_GAP = 70                 # 题目行间距
OPT_FIRST_X = 140                 # 第一列选项框左侧的 x
OPT_COL_GAP = 25                  # 选项框之间的水平间距
OPT_PADDING = 6                   # 统计涂黑占比时向内收缩的边距（避开框线）


FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")


def system_font_dirs():
    """返回当前平台的字体目录列表（跨平台，不依赖外部命令）。"""
    dirs = []
    if sys.platform == "win32":
        win_root = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(win_root, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    elif sys.platform == "darwin":
        dirs += [
            "/System/Library/Fonts",
            "/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]
    else:
        dirs += [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
        ]
    return [d for d in dirs if os.path.isdir(d)]


def read_font_names(path):
    """读取字体文件中每个面（face）的家族名与全名。

    返回 [(面索引, 家族名, 全名), ...]。
    .ttc/.otc 是字体集合，一个文件含多个面（如 Noto Sans CJK 的 JP/KR/SC/TC/HK），
    Pillow 加载时必须用 index 指定面，因此这里把索引一并读出。

    注意：字体集合中各面共享同一个文件句柄，必须全部读完后统一关闭，
    不能在循环里逐面 close()，否则后续面会抛 "seek of closed file"。
    """
    collection = None
    faces = []
    try:
        if path.lower().endswith((".ttc", ".otc")):
            collection = TTCollection(path, lazy=True)
            fonts = collection.fonts
        else:
            collection = TTFont(path, lazy=True)
            fonts = [collection]

        for index, font in enumerate(fonts):
            family = full = None
            try:
                name_records = font["name"]
            except Exception:
                continue

            # nameID 1 = 家族名, 4 = 全名；英文(0x409) / 简体中文(0x804) 优先
            for nid, attr in ((1, "family"), (4, "full")):
                best = None
                for rec in name_records.names:
                    if rec.nameID != nid:
                        continue
                    try:
                        value = rec.toUnicode()
                    except Exception:
                        continue
                    if not value:
                        continue
                    best = value
                    if rec.langID in (0x409, 0x804):
                        break
                if attr == "family":
                    family = best
                else:
                    full = best

            if family:
                faces.append((index, family, full or family))
    except Exception:
        # 损坏的字体文件直接跳过，不影响其余字体
        return faces
    finally:
        if collection is not None:
            try:
                collection.close()
            except Exception:
                pass
    return faces


def iter_system_fonts():
    """遍历系统字体，逐条产出 (文件路径, 面索引, 家族名, 全名)。"""
    for directory in system_font_dirs():
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                if not fname.lower().endswith(FONT_EXTS):
                    continue
                path = os.path.join(root, fname)
                for index, family, full in read_font_names(path):
                    yield path, index, family, full


def _face_style_rank(path):
    """为字重/字形排序：常规体优先，斜体与其它变体靠后。

    字体集合里同一家族名可能出现在多个文件中（如 Regular / Bold / Oblique），
    这里让常规字重排到最前，保证选中稳定且符合常规排版预期。
    返回 (变体等级, )，等级越小越优先：0=常规, 1=未知, 2=变体。
    """
    stem = os.path.basename(path).lower()
    # 斜体、倾斜等字形变体一律靠后
    if any(w in stem for w in ("italic", "oblique", "slanted")):
        return 2
    if "regular" in stem or "book" in stem:
        return 0
    if any(w in stem for w in ("bold", "black", "heavy", "light", "thin",
                               "medium", "demibold", "semibold", "extrabold",
                               "extralight", "ultra")):
        return 2
    return 1


_FONT_INDEX_CACHE = {}


def build_font_index():
    """扫描系统字体并建立 {家族名/全名(小写): (路径, 面索引)} 索引。

    结果按进程缓存，避免每次生成答题卡都重新扫描字体目录。
    同一字体名出现多次时，常规字重优先（见 _face_style_rank）。
    """
    if _FONT_INDEX_CACHE:
        return _FONT_INDEX_CACHE

    best = {}
    for path, index, family, full in iter_system_fonts():
        rank = (_face_style_rank(path), index)
        for key in {family.lower(), full.lower()}:
            prev = best.get(key)
            if prev is None or rank < prev[0]:
                best[key] = (rank, path, index)

    _FONT_INDEX_CACHE.update(
        {key: (path, index) for key, (_rank, path, index) in best.items()})
    return _FONT_INDEX_CACHE


def resolve_font(font_spec):
    """把 config.json 中的 font 字段解析成 (文件路径, 面索引)。

    Pillow 的 truetype() 只认文件路径，不经过 fontconfig，
    因此直接写字体家族名（如 "Noto Sans CJK SC"）会报 cannot open resource。
    这里兼容两种写法：
      - 直接给出字体文件路径（.ttf/.otf/.ttc）时原样使用；
      - 给出家族名时，在系统字体索引中按字体内部记录的真实家族名匹配。
    匹配基于 fontTools 解析字体文件本身，Windows / macOS / Linux 通用，
    且能正确定位 .ttc 集合中的具体面（如 Noto Sans CJK 的简体中文面）。
    """
    if not font_spec:
        return None, 0

    # 已经是可用的文件路径
    if os.path.isfile(font_spec):
        return font_spec, 0

    target = font_spec.strip().lower()
    index = build_font_index()

    hit = index.get(target)
    if hit is not None:
        return hit

    # 退而求其次：按子串做模糊匹配
    for key, (path, face_index) in index.items():
        if target in key:
            return path, face_index

    return None, 0


def load_font(font_spec, size):
    """加载指定字号的字体，解析失败时退回 Pillow 内置字体。"""
    path, index = resolve_font(font_spec)
    if path is not None:
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            traceback.print_exc()
    print(f"[WARN] 无法加载字体 {font_spec!r}，已退回内置字体", file=sys.stderr)
    return ImageFont.load_default(size=size)



class AutoMarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("答题卡自动阅卷（实时摄像头+图片导入）")
        self.root.geometry("700x520")

        # 加载配置
        self.cfg_file = "config.json"
        self.cfg_template = "config.json.example"
        self.config = self.load_config(show_message=False)

        # 标准答题卡尺寸（与 gen_sheet 中一致）
        self.standard_w = SHEET_W
        self.standard_h = SHEET_H

        # UI控件
        frame_btn = tk.Frame(root)
        frame_btn.pack(pady=10)

        tk.Button(frame_btn, text="重新加载JSON配置", command=self.load_config, width=20).grid(row=0, column=0, padx=5, pady=4)
        tk.Button(frame_btn, text="动态生成答题卡", command=self.gen_sheet, width=20).grid(row=0, column=1, padx=5, pady=4)
        tk.Button(frame_btn, text="开启实时摄像头阅卷", command=self.live_camera_scan, width=22).grid(row=1, column=0, padx=5, pady=4)
        tk.Button(frame_btn, text="导入图片文件阅卷", command=self.image_file_scan, width=22).grid(row=1, column=1, padx=5, pady=4)

        self.sheet_save_path = "answer_sheet.png"

    def ensure_config_file(self):
        """确保 config.json 存在。

        config.json 属于本地配置，不入版本库（见 .gitignore），
        首次运行或新克隆仓库时从 config.json.example 复制一份。
        返回是否成功就绪。
        """
        if os.path.isfile(self.cfg_file):
            return True

        template = getattr(self, "cfg_template", "config.json.example")
        if os.path.isfile(template):
            try:
                shutil.copyfile(template, self.cfg_file)
                print(f"[INFO] 已从 {template} 创建 {self.cfg_file}", file=sys.stderr)
                return True
            except OSError:
                traceback.print_exc()

        return False

    def load_config(self, show_message=True):
        if not self.ensure_config_file():
            messagebox.showerror(
                "配置缺失",
                f"未找到 {self.cfg_file}，且无法从 {self.cfg_template} 创建。\n"
                f"请手动复制 {self.cfg_template} 为 {self.cfg_file} 后重试。")
            self.config = self.default_config()
            return self.config

        try:
            with open(self.cfg_file, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            if show_message:
                messagebox.showinfo("成功", f"加载配置：{self.config['question_count']}题，每题{self.config['option_count']}个选项")
            return self.config
        except Exception as e:
            messagebox.showerror("配置读取失败", str(e))
            self.config = self.default_config()
            return self.config

    @staticmethod
    def default_config():
        """读取失败时的兜底配置。"""
        return {
            "question_count": 5,
            "option_count": 4,
            "option_text": ["A", "B", "C", "D"],
            "standard_answer": {},
            "fill_ratio_threshold": 0.45,
            "font": "SimHei"
        }

    # 动态生成答题卡
    def gen_sheet(self):
        width, height = self.standard_w, self.standard_h
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        font_title = load_font(self.config['font'], 36)
        font_text = load_font(self.config['font'], 26)

        draw.text((320, 40), "标准化答题卡", font=font_title, fill="black")

        box_w, box_h = OPT_BOX_W, OPT_BOX_H
        start_y = OPT_FIRST_Y
        line_gap = OPT_LINE_GAP
        x_question = 60
        x_start_box = OPT_FIRST_X

        q_num = self.config["question_count"]
        opt_num = self.config["option_count"]
        opt_labels = self.config["option_text"]

        for q in range(q_num):
            draw.text((x_question, start_y + 5), f"{q+1}.", font=font_text, fill="black")
            current_x = x_start_box
            for opt in range(opt_num):
                draw.rectangle([current_x, start_y, current_x + box_w, start_y + box_h], outline="black", width=3)
                text = opt_labels[opt]
                bbox = draw.textbbox((0, 0), text, font=font_text)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                text_x = current_x + (box_w - tw) / 2
                text_y = start_y + (box_h - th) / 2
                draw.text((text_x, text_y), text, font=font_text, fill="black")
                current_x += box_w + OPT_COL_GAP
            start_y += line_gap

        draw.rectangle([FRAME_MARGIN, FRAME_MARGIN,
                        width - FRAME_MARGIN, height - FRAME_MARGIN],
                       outline="black", width=FRAME_THICKNESS)
        img.save(self.sheet_save_path)
        messagebox.showinfo("完成", f"答题卡已生成：{self.sheet_save_path}")

    def get_option_cells(self):
        """按 config.json 动态计算每题每个选项框在标准答题卡上的绝对坐标。

        返回与 gen_sheet 完全同源的布局：
        [[(x, y, w, h), ...每题的各选项...], ...每题一组]
        坐标基于标准尺寸 (standard_w, standard_h)，与透视矫正后的图像一一对应。
        """
        q_num = self.config["question_count"]
        opt_num = self.config["option_count"]

        cells = []
        for q in range(q_num):
            y = OPT_FIRST_Y + q * OPT_LINE_GAP
            row = []
            for opt in range(opt_num):
                x = OPT_FIRST_X + opt * (OPT_BOX_W + OPT_COL_GAP)
                row.append((x, y, OPT_BOX_W, OPT_BOX_H))
            cells.append(row)
        return cells

    # ==================== 透视变换与边缘检测 ====================

    def find_corners_by_lines(self, edged):
        """通过霍夫直线寻找答题卡的4个角点"""
        # 使用概率霍夫变换检测直线
        lines = cv2.HoughLinesP(edged, 1, np.pi / 180, threshold=80, minLineLength=100, maxLineGap=20)
        if lines is None:
            return None

        horizontal_lines = []
        vertical_lines = []

        # 区分水平和垂直直线
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.abs(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
            if angle < 45 or angle > 135:
                horizontal_lines.append((x1, y1, x2, y2))
            else:
                vertical_lines.append((x1, y1, x2, y2))

        if len(horizontal_lines) < 2 or len(vertical_lines) < 2:
            return None

        # 取最靠上、最靠下、最靠左、最靠右的直线
        top_line = min(horizontal_lines, key=lambda l: min(l[1], l[3]))
        bottom_line = max(horizontal_lines, key=lambda l: max(l[1], l[3]))
        left_line = min(vertical_lines, key=lambda l: min(l[0], l[2]))
        right_line = max(vertical_lines, key=lambda l: max(l[0], l[2]))

        def intersection(line1, line2):
            x1, y1, x2, y2 = line1
            x3, y3, x4, y4 = line2
            denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if denom == 0:
                return None
            px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
            py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denom
            return [px, py]

        tl = intersection(top_line, left_line)
        tr = intersection(top_line, right_line)
        bl = intersection(bottom_line, left_line)
        br = intersection(bottom_line, right_line)

        if None in [tl, tr, bl, br]:
            return None

        return np.array([tl, tr, br, bl], dtype="float32")

    def auto_warp_perspective(self, img_bgr):
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blur, 50, 150)
        
        img_h, img_w = img_bgr.shape[:2]
        
        contours, _ = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        screen_cnt = None
        
        # 遍历面积最大的前5个轮廓
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                # 【关键升级1】：不能直接拿来用，必须校验检测到的四边形是否合理
                pts = approx.reshape(4, 2)
                rect = np.zeros((4, 2), dtype="float32")
                s = pts.sum(axis=1)
                rect[0] = pts[np.argmin(s)]
                rect[2] = pts[np.argmax(s)]
                diff = np.diff(pts, axis=1)
                rect[1] = pts[np.argmin(diff)]
                rect[3] = pts[np.argmax(diff)]
                
                (tl, tr, br, bl) = rect
                # 修复正确的宽高计算公式
                w1 = int(np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2)))
                w2 = int(np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2)))
                h1 = int(np.sqrt(((bl[0] - tl[0]) ** 2) + ((bl[1] - tl[1]) ** 2)))
                h2 = int(np.sqrt(((br[0] - tr[0]) ** 2) + ((br[1] - tr[1]) ** 2)))
                
                maxW = max(w1, w2)
                maxH = max(h1, h2)
                
                # 【关键升级2】：合理性校验
                # 1. 面积不能小于原图的 20%（防止截取到局部细节）
                # 2. 长宽比不能极端夸张（防止截取到边缘残缺的细长梯形）
                if maxW > 0 and maxH > 0:
                    area_ratio = (maxW * maxH) / (img_w * img_h)
                    aspect_ratio = maxW / maxH if maxW > maxH else maxH / maxW
                    if area_ratio > 0.2 and aspect_ratio < 5.0:
                        screen_cnt = approx
                        break # 找到合理的完整边框，跳出循环

        # 【关键升级3】：如果多边形拟合失败，或拟合出的四边形不合理（残缺），启用霍夫直线法
        if screen_cnt is None:
            screen_cnt = self.find_corners_by_lines(edged)
            
        if screen_cnt is None:
            return None

        pts = screen_cnt if not isinstance(screen_cnt, np.ndarray) or screen_cnt.shape == (4, 2) else screen_cnt.reshape(4, 2)
        
        # 再次进行四点排序（处理霍夫直线返回的数据）
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]

        (tl, tr, br, bl) = rect

        # 只做外框定位：把检测到外框的四角，映射到 gen_sheet 画出的外框位置
        # gen_sheet 的外框中心线位于 [FRAME_MARGIN, FRAME_MARGIN] ~ [W-FRAME_MARGIN, H-FRAME_MARGIN]
        # 映射到该矩形，再整体缩放到标准尺寸，即可与生成图逐像素对齐，
        # 从而让 get_option_cells() 算出的固定相对坐标直接可用。
        left = float(FRAME_MARGIN)
        top = float(FRAME_MARGIN)
        right = float(self.standard_w - FRAME_MARGIN)
        bottom = float(self.standard_h - FRAME_MARGIN)

        dst = np.array([[left, top], [right, top], [right, bottom], [left, bottom]],
                       dtype="float32")

        # 直接在标准尺寸画布上完成透视变换，避免二次 resize 引入偏差
        M = cv2.getPerspectiveTransform(rect, dst)
        warp_standard = cv2.warpPerspective(
            img_bgr, M, (self.standard_w, self.standard_h),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

        transform_info = {
            'matrix': M,
            'rect': rect,
            'dst_rect': dst,
            'orig_w': self.standard_w,
            'orig_h': self.standard_h
        }

        return warp_standard, transform_info

    # 只定位外框；选项位置由 config.json 动态生成的固定相对坐标决定
    def mark_sheet_image(self, img_warped, transform_info):
        # 读取环境变量，开启调试日志
        DEBUG = os.getenv("AUTOMARKAPP_DEBUG", "").lower() in ["1", "true", "yes"]
        DEBUG_BIN = os.getenv("AUTOMARKAPP_DEBUG_OUTBIN", "").lower() in ["1", "true", "yes"]

        gray = cv2.cvtColor(img_warped, cv2.COLOR_BGR2GRAY)
        # 取消反向！白底黑字正常二值化
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray_enhanced = clahe.apply(gray)
        _, binary = cv2.threshold(gray_enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 形态学闭运算，把细线补全
        kernel = np.ones((3, 3), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        if DEBUG_BIN:
            cv2.imwrite("debug_binary.png", binary)

        # 选项框坐标直接由 config.json 推导，与 gen_sheet 完全同源，
        # 不再依赖图像轮廓检测，因此行距/题量的任何变化都会自动同步。
        cells = self.get_option_cells()

        q_total = self.config["question_count"]
        threshold = self.config["fill_ratio_threshold"]
        std_ans = self.config["standard_answer"]

        # 展平成与外框坐标系一致的列表，供调用方可视化与统计
        box_list = [cell for row in cells for cell in row]

        img_h, img_w = binary.shape[:2]
        correct = 0
        user_answer = {}

        for q_idx in range(q_total):
            best_opt = -1
            max_ratio = 0.0
            for opt_idx, (x, y, w, h) in enumerate(cells[q_idx]):
                # 向内收缩，避开框线本身（否则空框也会被算作涂黑）
                x1 = max(0, x + OPT_PADDING)
                y1 = max(0, y + OPT_PADDING)
                x2 = min(img_w, x + w - OPT_PADDING)
                y2 = min(img_h, y + h - OPT_PADDING)
                roi = binary[y1:y2, x1:x2]

                total_pix = roi.shape[0] * roi.shape[1]
                if total_pix <= 0:
                    continue
                black_pix = total_pix - cv2.countNonZero(roi)
                ratio = black_pix / total_pix

                if DEBUG:
                    print(f"[DEBUG] 第{q_idx+1}题 选项{opt_idx} 涂黑占比: {ratio:.3f}")

                if ratio > max_ratio:
                    max_ratio = ratio
                    best_opt = opt_idx

            if max_ratio >= threshold:
                user_answer[str(q_idx)] = best_opt
            else:
                user_answer[str(q_idx)] = -1

            if DEBUG:
                print(f"[DEBUG] 第{q_idx+1}题最终选择: {user_answer[str(q_idx)]}")

        # 统计得分
        for key in std_ans:
            if user_answer.get(key, -1) == std_ans[key]:
                correct += 1

        score = round((correct / q_total) * 100, 2) if q_total else 0.0

        if DEBUG:
            print(f"[DEBUG] 考生答案集合: {user_answer}")
            print(f"[DEBUG] 正确题数: {correct}/{q_total}，最终得分: {score}")

        return user_answer, score, box_list


    # ==================== 实时摄像头动态扫描（带可视化） ====================

    def live_camera_scan(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            messagebox.showerror("错误", "无法打开摄像头")
            return
        score_history = []  # 存最近5帧的分数
        answer_history = []  # 存最近5帧的答案字典
        STABLE_FRAMES = 5   # 需要连续稳定多少帧才更新显示

        expected_boxes = self.config['question_count'] * self.config['option_count']
        while True:
            ret, frame = cap.read()
            if not ret: break
            warp_img = None
            transform_info = None
            box_list = []
            score = 0.0
            ans_dict = {}

            try:
                result = self.auto_warp_perspective(frame)
            except Exception:
                traceback.print_exc()
                result = None

            if result is not None:
                warp_img, transform_info = result
                ans_dict, score, box_list = self.mark_sheet_image(warp_img, transform_info)
                # 选项坐标是固定的，满额即视为定位成功，只要求外框可用
                if len(box_list) != expected_boxes:
                    score_text = "Checking..."
                else:
                    # 存入历史
                    score_history.append(score)
                    answer_history.append(ans_dict)
                    if len(score_history) > STABLE_FRAMES:
                        score_history.pop(0)
                        answer_history.pop(0)

                    # 判断是否稳定：最近STABLE_FRAMES帧的分数是否一致
                    if len(score_history) == STABLE_FRAMES and len(set(score_history)) == 1:
                        stable_score = score_history[0]
                        score_text = f"Score: {stable_score} (Stable)"
                    else:
                        # 还没稳定，显示"检测中"
                        if len(score_history) > 0:
                            score_text = f"Score: {score_history[-1]} (Checking...)"
                        else:
                            score_text = "Checking..."
            else:
                score_history.clear()
                answer_history.clear()
                score_text = "No AnswerSheet Detected"

            if warp_img is not None and transform_info is not None:
                rect = transform_info['rect']

                # 1. 绘制答题卡外框（绿色）
                pts_int = np.int32(rect)
                cv2.polylines(frame, [pts_int], True, (0, 255, 0), 3)

                # 2. 将固定坐标下的选项方框，逆变换回原图坐标并绘制。
                #    标准图坐标系 -> 外框四角，与 auto_warp_perspective 的 dst 完全对应。
                if box_list:
                    inv_M = cv2.getPerspectiveTransform(
                        np.array(transform_info['dst_rect'], dtype="float32"), rect)

                    for (bx, by, bw, bh) in box_list:
                        box_pts = np.array([
                            [bx, by],
                            [bx + bw, by],
                            [bx + bw, by + bh],
                            [bx, by + bh]
                        ], dtype="float32")

                        orig_pts = cv2.perspectiveTransform(box_pts.reshape(1, -1, 2), inv_M)
                        orig_pts_int = np.int32(orig_pts.reshape(-1, 2))

                        # 绘制选项方框（蓝色）
                        cv2.polylines(frame, [orig_pts_int], True, (255, 100, 0), 2)

                # 显示固定的选项框数量
                cv2.putText(frame, f"Boxes: {len(box_list)}/{expected_boxes}", (30, 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 200, 255), 2)

            cv2.putText(frame, score_text, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 3)
            cv2.imshow("Real-Time Scanning | Press Q to Exit", frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        cap.release()
        cv2.destroyAllWindows()

    # ==================== 导入本地图片阅卷 ====================

    def image_file_scan(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("图片文件", "*.jpg;*.png;*.jpeg;*.bmp")]
        )
        if not file_path:
            return

        img = cv2.imread(file_path)
        if img is None:
            messagebox.showerror("读取失败", f"无法读取图片：{file_path}")
            return

        try:
            warp_img, transform_info = self.auto_warp_perspective(img)
        except Exception:
            traceback.print_exc()
            warp_img = None
        if warp_img is None:
            messagebox.showerror("识别失败", "未检测到答题卡外框，请摆正图片")
            return

        ans_dict, score, _ = self.mark_sheet_image(warp_img, transform_info)
        if ans_dict is None:
            messagebox.showerror("识别失败", "读取答题卡答案时出现错误")
            return

        ans_readable = ""
        std_ans = self.config['standard_answer']
        for i in range(self.config['question_count']):
            chosen = ans_dict.get(str(i), -1)
            label = "未作答" if chosen == -1 else self.config['option_text'][chosen]
            correct = chosen == std_ans.get(str(i))
            ans_readable = f"{ans_readable}\n第{i+1}题：{label} {'(正确)' if correct else ''}"
        messagebox.showinfo("阅卷结果", f"得分：{score} 分\n考生答案：{ans_readable}")


if __name__ == "__main__":
    root = tk.Tk()
    app = AutoMarkApp(root)
    root.mainloop()
