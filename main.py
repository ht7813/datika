import tkinter as tk
from tkinter import filedialog, messagebox
import cv2
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import traceback
import os

class AutoMarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("答题卡自动阅卷（实时摄像头+图片导入）")
        self.root.geometry("700x520")

        # 加载配置
        self.cfg_file = "config.json"
        self.config = self.load_config()

        # 标准答题卡尺寸（与 gen_sheet 中一致）
        self.standard_w = 900
        self.standard_h = 1200

        # UI控件
        frame_btn = tk.Frame(root)
        frame_btn.pack(pady=10)

        tk.Button(frame_btn, text="重新加载JSON配置", command=self.load_config, width=20).grid(row=0, column=0, padx=5, pady=4)
        tk.Button(frame_btn, text="动态生成答题卡", command=self.gen_sheet, width=20).grid(row=0, column=1, padx=5, pady=4)
        tk.Button(frame_btn, text="开启实时摄像头阅卷", command=self.live_camera_scan, width=22).grid(row=1, column=0, padx=5, pady=4)
        tk.Button(frame_btn, text="导入图片文件阅卷", command=self.image_file_scan, width=22).grid(row=1, column=1, padx=5, pady=4)

        self.sheet_save_path = "answer_sheet.png"

    def load_config(self):
        try:
            with open(self.cfg_file, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            messagebox.showinfo("成功", f"加载配置：{self.config['question_count']}题，每题{self.config['option_count']}个选项")
            return self.config
        except Exception as e:
            messagebox.showerror("配置读取失败", str(e))
            self.config = {
                "question_count": 5,
                "option_count": 4,
                "option_text": ["A", "B", "C", "D"],
                "standard_answer": {},
                "fill_ratio_threshold": 0.45,
                "font": "SimHei"
            }
            return self.config

    # 动态生成答题卡
    def gen_sheet(self):
        width, height = self.standard_w, self.standard_h
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        try:
            font_title = ImageFont.truetype(self.config['font'], 36)
            font_text = ImageFont.truetype(self.config['font'], 26)
        except:
            traceback.print_exc()
            font_title = ImageFont.load_default(size=36)
            font_text = ImageFont.load_default(size=26)

        draw.text((320, 40), "标准化答题卡", font=font_title, fill="black")

        box_w, box_h = 55, 42
        start_y = 120
        line_gap = 70
        x_question = 60
        x_start_box = 140

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
                current_x += box_w + 25
            start_y += line_gap

        draw.rectangle([20, 20, width-20, height-20], outline="black", width=5)
        img.save(self.sheet_save_path)
        messagebox.showinfo("完成", f"答题卡已生成：{self.sheet_save_path}")

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
        
        # 修复宽高计算
        maxW = max(int(np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))),
                   int(np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))))
        maxH = max(int(np.sqrt(((bl[0] - tl[0]) ** 2) + ((bl[1] - tl[1]) ** 2))),
                   int(np.sqrt(((br[0] - tr[0]) ** 2) + ((br[1] - tr[1]) ** 2))))
                   
        dst = np.array([[0, 0], [maxW, 0], [maxW, maxH], [0, maxH]], dtype="float32")
        M = cv2.getPerspectiveTransform(rect, dst)
        warp = cv2.warpPerspective(img_bgr, M, (maxW, maxH))
        warp_standard = cv2.resize(warp, (self.standard_w, self.standard_h), interpolation=cv2.INTER_AREA)
        scale_x = maxW / self.standard_w
        scale_y = maxH / self.standard_h
        transform_info = {
            'matrix': M,
            'rect': rect,
            'scale_x': scale_x,
            'scale_y': scale_y,
            'orig_w': maxW,
            'orig_h': maxH
        }

        return warp_standard, transform_info

    # 自动寻找答题卡外框 + 四点透视矫正
    def mark_sheet_image(self, img_warped, transform_info):
        # 读取环境变量，开启调试日志
        DEBUG = os.getenv("AUTOMARKAPP_DEBUG", "").lower() in ["1", "true", "yes"]
        DEBUG_BIN = os.getenv("AUTOMARKAPP_DEBUG_OUTBIN", "").lower() in ["1", "true", "yes"]
        
        gray = cv2.cvtColor(img_warped, cv2.COLOR_BGR2GRAY)
        # 取消反向！白底黑字正常二值化
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray_enhanced = clahe.apply(gray)
        _, binary = cv2.threshold(gray_enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 形态学闭运算，把细线补全
        kernel = np.ones((3,3), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        if DEBUG_BIN: cv2.imwrite("debug_binary.png", binary)

        contours, _ = cv2.findContours(binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
        box_list = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if DEBUG: print(f"raw rect w:{w} h:{h}")
            if w > 200 or h > 200: continue
            if w < 25 or h < 25: continue
            if 45 < w < 75 and 35 < h < 55:
                box_list.append((x, y, w, h))
        # 按横坐标、纵坐标聚类，只保留面积最大的外框
        def is_overlapping(box1, box2, iou_thresh=0.5):
            x1, y1, w1, h1 = box1
            x2, y2, w2, h2 = box2
            # 计算交集
            ix1, iy1 = max(x1, x2), max(y1, y2)
            ix2, iy2 = min(x1+w1, x2+w2), min(y1+h1, y2+h2)
            if ix2 <= ix1 or iy2 <= iy1:
                return False
            inter_area = (ix2-ix1) * (iy2-iy1)
            box1_area, box2_area = w1*h1, w2*h2
            iou = inter_area / (box1_area + box2_area - inter_area)
            return iou > iou_thresh
        unique_box = []
        '''for (x, y, w, h) in box_list:
            # 坐标取整，把同一方框的内外框合并到一组
            key = (round(x, -1), round(y, -1))
            area = w * h
            if key not in unique_box or area > unique_box[key][2] * unique_box[key][3]:
                unique_box[key] = (x, y, w, h)'''
        for box in sorted(box_list, key=lambda b: b[2]*b[3], reverse=True):  # 从大到小
            if not any(is_overlapping(box, existing) for existing in unique_box):
                unique_box.append(box)
        
        box_list = unique_box.copy()
        # 重新排序
        box_list.sort(key=lambda b: (round(b[1]/70), b[0]))

        if DEBUG:
            print(f"[DEBUG] 检测到候选方框总数: {len(box_list)}")
        
        if len(box_list) < self.config['option_count']:
            if DEBUG:
                print("[DEBUG] 方框数量不足，识别失败")
            return None, 0, box_list
    
        # 从上到下、从左到右排序方框
        # box_list.sort(key=lambda b: (b[1], b[0]))
        
        if DEBUG:
            print(f"[DEBUG] 排序后方框坐标列表: {box_list}")
    
        q_total = self.config["question_count"]
        opt_total = self.config["option_count"]
        threshold = self.config["fill_ratio_threshold"]
        std_ans = self.config["standard_answer"]
    
        correct = 0
        user_answer = {}
    
        for q_idx in range(q_total):
            slice_box = box_list[q_idx * opt_total: (q_idx + 1) * opt_total]
            best_opt = -1
            max_ratio = 0
            padding = 6
            for opt_idx, (x, y, w, h) in enumerate(slice_box):
                roi = binary[y+padding:y+h-padding, x+padding:x+w-padding]
                total_pix = roi.shape[0] * roi.shape[1]
                black_pix = total_pix - cv2.countNonZero(roi)
                if total_pix == 0:
                    max_ratio = 0
                    best_opt = -1
                    continue
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
        
        score = round((correct / q_total) * 100, 2)
        
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
        while True:
            ret, frame = cap.read()
            if not ret: break
            warp_img = None
            transform_info = None
            box_list = []
            score = 0.0
            ans_dict = {}
            
            result = self.auto_warp_perspective(frame)
            if result is not None:
                warp_img, transform_info = result
                ans_dict, score, box_list = self.mark_sheet_image(warp_img, transform_info)
                if len(box_list) != self.config['question_count'] * self.config['option_count']: 
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
                        stable_answer = answer_history[0]  # 取最新一帧的答案
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

            if warp_img is not None:
                #ans_dict, score, box_list = self.mark_sheet_image(warp_img, transform_info)
                #score_text = f"Score: {score}"

                # 在原图上绘制可视化边框
                if transform_info is not None:
                    rect = transform_info['rect']

                    # 1. 绘制答题卡外框（绿色）
                    pts_int = np.int32(rect)
                    cv2.polylines(frame, [pts_int], True, (0, 255, 0), 3)

                    # 2. 将标准尺寸下检测到的选项方框，逆变换回原图坐标并绘制
                    if box_list:
                        sx = transform_info['scale_x']
                        sy = transform_info['scale_y']

                        # 预计算逆变换矩阵
                        dst_pts = np.array([[0, 0],
                                            [transform_info['orig_w'], 0],
                                            [transform_info['orig_w'], transform_info['orig_h']],
                                            [0, transform_info['orig_h']]], dtype="float32")
                        inv_M = cv2.getPerspectiveTransform(dst_pts, rect)

                        for (bx, by, bw, bh) in box_list:
                            real_x = bx * sx
                            real_y = by * sy
                            real_w = bw * sx
                            real_h = bh * sy

                            box_pts = np.array([
                                [real_x, real_y],
                                [real_x + real_w, real_y],
                                [real_x + real_w, real_y + real_h],
                                [real_x, real_y + real_h]
                            ], dtype="float32")

                            orig_pts = cv2.perspectiveTransform(box_pts.reshape(1, -1, 2), inv_M)
                            orig_pts_int = np.int32(orig_pts.reshape(-1, 2))

                            # 绘制选项方框（蓝色）
                            cv2.polylines(frame, [orig_pts_int], True, (255, 100, 0), 2)

                    # 显示检测到的方框数量
                    cv2.putText(frame, f"Boxes: {len(box_list)}", (30, 100),
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
        warp_img, transform_info = self.auto_warp_perspective(img)

        if warp_img is None:
            messagebox.showerror("识别失败", "未检测到答题卡边框，请摆正图片")
            return

        ans_dict, score, _ = self.mark_sheet_image(warp_img, transform_info)
        messagebox.showinfo("阅卷结果", f"得分：{score} 分\n考生答案：{ans_dict}")


if __name__ == "__main__":
    root = tk.Tk()
    app = AutoMarkApp(root)
    root.mainloop()
