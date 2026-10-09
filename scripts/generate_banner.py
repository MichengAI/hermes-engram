"""旧版 Pillow 排版草稿。正式 banner 由生图工具生成，本脚本不得覆盖它。"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    parser.add_argument("--bold-font", default="C:/Windows/Fonts/msyhbd.ttc")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    width, height, scale = 1500, 500, 2
    image = Image.new("RGB", (width * scale, height * scale))
    pixels = image.load()
    assert pixels is not None
    for x in range(width * scale):
        t = x / (width * scale - 1)
        color = (int(6 + 17 * t), int(17 + 3 * t), int(35 + 33 * t))
        for y in range(height * scale):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image)
    def box(bounds, fill, outline, radius=14, line=1):
        draw.rounded_rectangle(tuple(int(v * scale) for v in bounds), radius=radius * scale,
                               fill=fill, outline=outline, width=line * scale)
    def text(pos, value, size, color="#eaf3ff", bold=False, max_width=None):
        font = ImageFont.truetype(args.bold_font if bold else args.font, size * scale)
        bounds = draw.textbbox((pos[0] * scale, pos[1] * scale), value, font=font)
        # 双语文案变长时明确报错，避免生成被截断或挤出卡片的设计资源。
        if max_width is not None and bounds[2] - bounds[0] > max_width * scale:
            raise ValueError(f"Banner 文案超过预留宽度：{value}")
        if bounds[0] < 0 or bounds[1] < 0 or bounds[2] > image.width or bounds[3] > image.height:
            raise ValueError(f"Banner 文案超出画布：{value}")
        draw.text((pos[0] * scale, pos[1] * scale), value, font=font, fill=color)
    def line(points, color="#24456b", thick=2):
        draw.line([(int(x * scale), int(y * scale)) for x, y in points], fill=color, width=thick * scale)
    # 星点与细轮廓呼应 DSH 系列，而非使用不真实的界面截图。
    for x, y in ((30, 245), (520, 23), (610, 70), (650, 320), (1430, 42), (1460, 304), (840, 350), (1050, 33)):
        draw.ellipse((x * scale, y * scale, (x + 2) * scale, (y + 2) * scale), fill="#4b84b6")
    box((1, 1, 1498, 498), None, "#357bd0", radius=20)
    box((38, 32, 594, 70), "#092137", "#06d6ef", radius=19)
    text((55, 39), "Hermes Agent memory / 社区记忆插件", 17, "#dceeff", True, max_width=520)
    text((38, 99), "Hermes Engram", 56, bold=True, max_width=610)
    # 英文为主、中文辅助，两种语言共享同一品牌画面和功能层级。
    text((40, 179), "Project memory that stays with you.", 28, "#29d8f5", True, max_width=610)
    text((40, 223), "让项目记忆，跟得上每一轮对话", 22, "#9ce8f3", True, max_width=610)
    text((40, 272), "Remember decisions, fixes, and lessons.", 18, bold=True, max_width=610)
    text((40, 304), "记住决定、修复与经验", 17, "#a8bdd6", max_width=610)
    box((40, 344, 162, 376), "#092137", "#03bedf", radius=16)
    text((54, 349), "MIT / 开源", 15, max_width=98)
    box((177, 344, 423, 376), "#111d3a", "#6686ff", radius=16)
    text((190, 350), "Native provider / 原生插件", 14, max_width=220)
    # 右侧用职责关系图，不暗示尚未验证的产品界面。
    for x, y in ((691, 88), (1135, 88), (691, 246), (1135, 246)):
        line([(x + 102, y + 55), (1035, 196)], "#215a80")
    box((900, 123, 1120, 277), "#081f32", "#11ddf0", line=2)
    text((928, 149), "Engram", 31, "#5fe5f3", True, max_width=164)
    text((935, 201), "Project memory", 17, max_width=152)
    text((960, 232), "项目记忆", 17, "#abc1d8", max_width=130)
    for x, y, title, desc, accent in (
        (686, 70, "Project context", "当前项目", "#13d4f2"),
        (1150, 70, "Trusted saves", "确认来源再保存", "#7390ff"),
        (686, 260, "Session continuity", "会话延续", "#9d6cff"),
        (1150, 260, "Session summaries", "会话摘要归档", "#d260fa"),
    ):
        box((x, y, x + 206, y + 94), "#0a192e", accent)
        line([(x + 12, y + 10), (x + 194, y + 10)], accent, 2)
        text((x + 15, y + 25), title, 17, bold=True, max_width=176)
        text((x + 15, y + 58), desc, 16, "#abc1d8", max_width=176)
    labels = [("Project recall", "按项目召回", "#07d4ee"), ("Source checks", "来源确认 · 少记不猜", "#6486fb"),
              ("Private blocks", "隐私遮蔽 · 标签约定", "#986af2"), ("Session continuity", "会话延续 · 结束保护", "#efaa3e"),
              ("Optional features", "可选增强 · 能力检查", "#d958eb")]
    for index, (title, subtitle, accent) in enumerate(labels):
        x = 38 + index * 292
        box((x, 396, x + 274, 468), "#08192d", accent, radius=10)
        line([(x + 12, 405), (x + 262, 405)], accent, 2)
        text((x + 16, 409), title, 18, bold=True, max_width=242)
        text((x + 16, 438), subtitle, 14, "#a5bad1", max_width=242)
    path = root / "assets/branding/hermes-engram-banner-pillow-draft.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)
    with Image.open(path) as verified:
        assert verified.size == (width * scale, height * scale)
        verified.verify()
    print(f"Banner 已生成：{path}，尺寸 {width * scale}×{height * scale}")


if __name__ == "__main__":
    main()
