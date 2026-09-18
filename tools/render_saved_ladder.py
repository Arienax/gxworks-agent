import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json
import os

def run_real_api():
    json_path = "real_deepseek_output.json"
    
    if not os.path.exists(json_path):
        print(f"错误: 未找到真实数据文件 {json_path}")
        print("请先启动软件并成功执行一次‘开始编译’，以捕获真实的 DeepSeek 返回数据。")
        return

    print(f"成功读取真实大模型数据: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        try:
            json_data = json.load(f)
        except Exception as e:
            print(f"JSON 语法解析失败（大模型返回的内容可能格式不完整）: {e}")
            return

    
    from rendering.ladder import AdvancedSVGLadder, generate_gx_works2_csv

    print("\n--- [1. 真实数据 -> CSV 语句表转换测试] ---")
    csv_success = generate_gx_works2_csv(json_data, "real_import_program.csv")
    if csv_success:
        print("-> 成功：标准无注解语句表已导出至 'real_import_program.csv'")

    print("\n--- [2. 真实数据 -> SVG 梯形图几何渲染测试] ---")
    try:
        drawer = AdvancedSVGLadder()
        svg_content = drawer.generate_ladder(json.dumps(json_data))
        # 导出独立的测试 SVG
        with open("real_ladder_output.svg", "w", encoding="utf-8") as f:
            f.write(svg_content)
        print("-> 成功：真实梯形图已绘制并输出至 'real_ladder_output.svg'")
        print(f"-> 【真实图形尺寸】: 宽度 = {drawer.width} 像素, 高度 = {drawer.height} 像素")
        
    except Exception as e:
        print(f"-> 失败：真实数据在生成几何坐标时让绘图引擎崩溃了。")
        print(f"-> 崩溃节点位置/错误原因: {str(e)}")

if __name__ == "__main__":
    run_real_api()