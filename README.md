# datika

基于 OpenCV 的答题卡生成与自动阅卷工具，带 Tkinter 图形界面。按 `config.json` 动态生成标准化答题卡，再通过摄像头实时识别或导入图片批阅，自动比对标准答案并计分。

适合课堂小测、练习卷等选择题场景。

## 功能

- **动态生成答题卡**：按配置的题目数、选项数与选项文字绘制 900×1200 的标准化答题卡，保存为 `answer_sheet.png`。
- **实时摄像头阅卷**：打开摄像头连续扫描，画面中直接绘制答题卡外框与选项框，并显示得分。连续 5 帧得分一致才判定为稳定结果，避免画面抖动导致分数跳动。
- **图片文件阅卷**：导入 JPG / PNG / BMP 图片，逐题列出考生作答与对错，并给出总分。
- **透视矫正**：答题卡任意倾斜、旋转、透视变形均可识别，只依赖答题卡外框定位。
- **选项定位不依赖图像检测**：选项坐标由配置与版式常量推导，与生成侧共用同一份定义，因此改题量、改选项数后无需调整识别参数。

## 环境要求

- Python >= 3.14
- Tkinter（多数 Linux 发行版需单独安装，如 `sudo pacman -S tk`）
- 摄像头（仅实时阅卷功能需要）

若系统没有安装 Unicode 中文字体，答题卡上的中文会退化为内置位图字体。

## 安装

```bash
git clone https://github.com/ht7813/datika
cd datika
uv sync
```

或使用 pip：

```bash
pip install -r requirements.txt
```

## 使用

```bash
uv run main.py
```

界面提供四个按钮：

| 按钮 | 作用 |
| --- | --- |
| 重新加载JSON配置 | 修改 `config.json` 后重新载入，无需重启程序 |
| 动态生成答题卡 | 按当前配置生成答题卡，保存为 `answer_sheet.png` |
| 开启实时摄像头阅卷 | 摄像头连续扫描，按 `q` 退出 |
| 导入图片文件阅卷 | 选择图片文件批阅并弹出结果 |

建议流程：

1. 编辑 `config.json`，设置题目数、选项数与标准答案。
2. 点击「动态生成答题卡」，打印生成的 `answer_sheet.png`。
3. 考生填涂后，用「开启实时摄像头阅卷」或「导入图片文件阅卷」批阅。

## 配置说明

配置文件 `config.json` 属本地配置，不入版本库。首次运行时会自动从 `config.json.example` 复制生成一份。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `question_count` | int | 题目数 |
| `option_count` | int | 每题选项数 |
| `option_text` | string[] | 选项文字，长度应与 `option_count` 一致 |
| `standard_answer` | object | 标准答案，键为题目序号（从 `0` 起），值为选项序号（从 `0` 起） |
| `fill_ratio_threshold` | float | 涂黑判定阈值，某选项框内黑色像素占比超过该值视为选中，默认 `0.45` |
| `font` | string | 字体家族名（如 `Noto Sans CJK SC`）或字体文件路径 |

示例：

```json
{
    "question_count": 6,
    "option_count": 4,
    "option_text": ["A", "B", "C", "D"],
    "standard_answer": {
        "0": 0,
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 0,
        "5": 1
    },
    "fill_ratio_threshold": 0.45,
    "font": "Noto Sans CJK SC"
}
```

关于字体：`font` 可填家族名，程序会扫描系统字体目录，按字体内部记录的真实家族名匹配，因而 Windows / macOS / Linux 通用；对 `.ttc` 字体集合（如 Noto Sans CJK 内含 JP/KR/SC/TC/HK 多个面）也能正确定位到具体面。也可以直接填字体文件路径。

## 调试

设置环境变量可输出识别过程信息：

| 变量 | 取值 | 作用 |
| --- | --- | --- |
| `AUTOMARKAPP_DEBUG` | `1` / `true` / `yes` | 打印每题每个选项的涂黑占比与最终选择 |
| `AUTOMARKAPP_DEBUG_OUTBIN` | `1` / `true` / `yes` | 输出二值化结果到 `debug_binary.png` |

示例：

```bash
AUTOMARKAPP_DEBUG=1 uv run main.py
```

## 项目结构

```
main.py               主程序（界面、答题卡生成、透视矫正、阅卷）
config.json.example   配置模板
config.json           本地配置（首次运行自动生成，不入版本库）
answer_sheet.png      生成的答题卡（运行时产出）
```

答题卡的版式参数（图纸尺寸、外框边距、选项框大小、行距等）集中在 `main.py` 顶部常量中，生成与识别两侧共用，修改后两边会同步生效。

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)
