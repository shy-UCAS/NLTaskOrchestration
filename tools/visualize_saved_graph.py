"""
tools/visualize_saved_graph.py
从保存的 BuiltGraph JSON 文件渲染 pyvis 交互式 HTML。

Usage:
    python -m tools.visualize_saved_graph path/to/graph.json [-o out.html] [--no-open]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcjp.graph_visualizer import visualize_from_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="BuiltGraph JSON 文件路径")
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="输出 HTML 路径（默认与 JSON 同目录同名 .html）")
    parser.add_argument("--no-open", action="store_true",
                        help="不自动在浏览器中打开")
    args = parser.parse_args()

    if not args.json_path.exists():
        print(f"Error: {args.json_path} not found", file=sys.stderr)
        return 1

    html_path = visualize_from_file(
        args.json_path,
        output_html_path=args.output,
        open_in_browser=not args.no_open,
    )
    print(f"HTML written to: {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
