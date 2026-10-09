"""生成与 DSH 系列一致的项目 Banner；Pillow 仅用于设计资源生成，不是插件运行依赖。"""
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
    def text(pos, value, size, color="#eaf3ff", bold=False):
        font = ImageFont.truetype(args.bold_font if bold else args.font, size * scale)
        draw.text((pos[0] * scale, pos[1] * scale), value, font=font, fill=color)
    def line(points, color="#24456b", thick=2):
        draw.line([(int(x * scale), int(y * scale)) for x, y in points], fill=color, width=thick * scale)
    # 星点与细轮廓呼应 DSH 系列，而非使用不真实的界面截图。
    for x, y in ((30, 245), (520, 23), (610, 70), (650, 320), (1430, 42), (1460, 304), (840, 350), (1050, 33)):
        draw.ellipse((x * scale, y * scale, (x + 2) * scale, (y + 2) * scale), fill="#4b84b6")
    box((1, 1, 1498, 498), None, "#357bd0", radius=20)
    box((38, 32, 423, 70), "#092137", "#06d6ef", radius=19)
    text((55, 38), "Hermes Agent 社区记忆插件", 19, "#dceeff", True)
    text((38, 99), "Hermes Engram", 56, bold=True)
    text((40, 181), "让项目记忆，跟得上每一轮对话", 29, "#29d8f5", True)
    text((40, 238), "自动召回决定、修复与约定", 23, bold=True)
    text((40, 278), "来源明确才保存，项目切换不串线。", 19, "#a8bdd6")
    box((40, 327, 142, 360), "#092137", "#03bedf", radius=16)
    text((55, 332), "MIT 开源", 16)
    box((157, 327, 297, 360), "#111d3a", "#6686ff", radius=16)
    text((172, 332), "原生 Provider", 16)
    # 右侧用正式职责关系图，不暗示尚未验证的产品界面。
    for x, y in ((691, 88), (1135, 88), (691, 246), (1135, 246)):
        line([(x + 102, y + 55), (1035, 196)], "#215a80")
    box((900, 123, 1120, 277), "#081f32", "#11ddf0", line=2)
    text((928, 150), "Engram", 31, "#5fe5f3", True)
    text((939, 201), "项目记忆库", 21)
    for x, y, title, desc, accent in (
        (686, 70, "当前项目", "Canonical 身份", "#13d4f2"),
        (1150, 70, "可靠存取", "来源绑定与脱敏", "#7390ff"),
        (686, 260, "会话恢复", "确认归属再继续", "#9d6cff"),
        (1150, 260, "压缩归档", "正式摘要与恢复", "#d260fa"),
    ):
        box((x, y, x + 206, y + 94), "#0a192e", accent)
        line([(x + 12, y + 10), (x + 194, y + 10)], accent, 2)
        text((x + 15, y + 25), title, 21, bold=True)
        text((x + 15, y + 60), desc, 15, "#abc1d8")
    labels = [("自动召回", "按项目读取", "#07d4ee"), ("来源保护", "少记，不猜", "#6486fb"),
              ("隐私脱敏", "private 块约定", "#986af2"), ("生命周期", "结束与回退保护", "#efaa3e"),
              ("可选增强", "服务端能力检查", "#d958eb")]
    for index, (title, subtitle, accent) in enumerate(labels):
        x = 38 + index * 292
        box((x, 396, x + 274, 468), "#08192d", accent, radius=10)
        line([(x + 12, 405), (x + 262, 405)], accent, 2)
        text((x + 16, 415), title, 20, bold=True)
        text((x + 141, 420), subtitle, 14, "#a5bad1")
    path = root / "assets/branding/hermes-engram-banner.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, optimize=True)
    with Image.open(path) as verified:
        assert verified.size == (width * scale, height * scale)
        verified.verify()
    print(f"Banner 已生成：{path}，尺寸 {width * scale}×{height * scale}")


if __name__ == "__main__":
    main()
