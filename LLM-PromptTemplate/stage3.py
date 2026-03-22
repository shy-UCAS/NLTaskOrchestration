# -*- coding: utf-8 -*-
"""
通用版原子事件生成代码
适配任意集群数量（4/5/6+），无需硬编码集群编号/动作/目标点
依赖：pandas、openpyxl（用于Excel导出）、json、re（Python内置）
安装依赖：pip install pandas openpyxl
"""
import json
import re
import pandas as pd
from typing import List, Dict, Optional

# ===================== 1. 通用工具函数（核心：动态解析，无硬编码） =====================
def extract_cluster_numbers(subject_str: str) -> List[int]:
    """
    动态提取执行主体中的集群编号（支持任意数量集群）
    :param subject_str: 执行主体字符串，如"5号集群" / "1+3+5号集群" / "所有集群"
    :return: 集群编号列表，如[5] / [1,3,5] / []（[]表示全集群）
    """
    try:
        # 匹配阿拉伯数字编号（如"5号"→5，"1+3+5号"→[1,3,5]）
        numbers = re.findall(r"(\d+)号", subject_str)
        cluster_nums = [int(num) for num in numbers] if numbers else []
        
        # 处理“所有/全部/四个/五个”等全集群表述
        full_cluster_keywords = ["所有", "全部", "四个", "五个", "六个", "七个"]
        if any(keyword in subject_str for keyword in full_cluster_keywords):
            return []  # 标记为全集群，后续补全编号
        
        return cluster_nums
    except Exception as e:
        print(f"提取集群编号失败：{e} | 输入字符串：{subject_str}")
        return []

def get_event_type(cluster_nums: List[int]) -> str:
    """
    动态判定事件类型（适配任意集群数量）
    :param cluster_nums: 集群编号列表，如[1] / [1,2] / [1,2,3,4,5]
    :return: 事件类型名称，如"5号集群独立事件" / "1+3+5号集群协同事件" / "全集群协同事件"
    """
    if len(cluster_nums) == 1:
        return f"{cluster_nums[0]}号集群独立事件"
    elif len(cluster_nums) > 1:
        cluster_str = "+".join([str(n) for n in cluster_nums])
        return f"{cluster_str}号集群协同事件"
    else:
        return "全集群协同事件"

def generate_event_id(cluster_nums: List[int], action_en: str, target: str) -> str:
    """
    动态生成原子事件唯一标识（无硬编码，适配任意集群/动作/目标）
    :param cluster_nums: 集群编号列表
    :param action_en: 动作英文标识（如attack/move/breakthrough/meet）
    :param target: 目标点（如hq_mark7/hq_5）
    :return: 标准化唯一标识，如cluster5_move_hqmark11_done
    """
    try:
        # 处理集群部分
        if len(cluster_nums) == 1:
            cluster_part = f"cluster{cluster_nums[0]}"
        elif len(cluster_nums) > 1:
            cluster_part = "_".join([f"cluster{n}" for n in cluster_nums])
        else:
            cluster_part = "all_clusters"  # 全集群标识
        
        # 目标点标准化（去掉下划线，如hq_mark7→hqmark7，保证标识统一）
        target_part = target.replace("_", "").strip()
        # 拼接唯一标识（固定后缀_done表示事件完成）
        event_id = f"{cluster_part}_{action_en}_{target_part}_done"
        
        return event_id
    except Exception as e:
        print(f"生成事件标识失败：{e} | 集群：{cluster_nums} 动作：{action_en} 目标：{target}")
        return ""

def generate_event_desc(cluster_nums: List[int], action_cn: str, target: str) -> str:
    """
    动态生成自然语言描述（适配任意集群/动作/目标）
    :param cluster_nums: 集群编号列表
    :param action_cn: 动作中文描述（如进攻/飞往/突破/会合）
    :param target: 目标点（如hq_mark7/hq_5）
    :return: 自然语言描述，如"5号集群独立飞往hq_mark11完成"
    """
    try:
        # 处理主体描述
        if len(cluster_nums) == 1:
            subject_desc = f"{cluster_nums[0]}号集群独立"
        elif len(cluster_nums) > 1:
            if len(cluster_nums) == 2:
                subject_desc = f"{cluster_nums[0]}号与{cluster_nums[1]}号集群"
            else:
                cluster_str = "+".join([str(n) for n in cluster_nums])
                subject_desc = f"{cluster_str}号集群共同"
        else:
            subject_desc = "所有集群共同"
        
        # 处理动作+目标描述（会合动作特殊适配）
        if action_cn == "会合":
            event_desc = f"{subject_desc}在{target}会合完成"
        else:
            event_desc = f"{subject_desc}{action_cn}{target}完成"
        
        # 清理冗余空格/字符
        return event_desc.replace("  ", " ").strip()
    except Exception as e:
        print(f"生成事件描述失败：{e} | 集群：{cluster_nums} 动作：{action_cn} 目标：{target}")
        return ""

# ===================== 2. 核心：通用原子事件生成逻辑 =====================
def generate_atomic_events_generic(
    structured_data: List[Dict], 
    all_cluster_count: Optional[int] = None
) -> Dict[str, List[Dict]]:
    """
    通用原子事件生成函数（适配任意集群数量）
    :param structured_data: 消歧+结构化拆分后的JSON数据（列表型字典）
    :param all_cluster_count: 总集群数（如5，用于补全“全集群”的编号）
    :return: 按事件类型分类的原子事件字典
    """
    # 通用动作映射（仅维护这一份，新增动作只需加一行）
    action_mapping = {
        "进攻": ("attack", "进攻"),
        "移动": ("move", "飞往"),
        "突破": ("breakthrough", "突破"),
        "会合": ("meet", "会合"),
        # 可扩展：新增动作类型只需在这里添加
        # "防御": ("defense", "防御"),
        # "侦查": ("recon", "侦查")
    }
    
    atomic_events = {}  # 最终存储结构：{事件类型: [事件1, 事件2,...]}
    seen_event_ids = set()  # 去重：避免重复生成相同事件
    
    # 遍历结构化数据，逐行生成原子事件
    for idx, item in enumerate(structured_data):
        try:
            # 提取核心字段（容错：字段缺失时赋默认值）
            subject = item.get("执行主体", "")
            action_types = item.get("动作类型", "").split("|")
            targets = item.get("目标点", "").split("|")
            
            # 跳过空值
            if not subject or not action_types or not targets:
                print(f"第{idx}行结构化数据字段为空，跳过 | 数据：{item}")
                continue
            
            # 动态提取集群编号
            cluster_nums = extract_cluster_numbers(subject)
            # 补全全集群的编号（如总集群数=5，则cluster_nums=[1,2,3,4,5]）
            if not cluster_nums and all_cluster_count:
                cluster_nums = list(range(1, all_cluster_count + 1))
            
            # 遍历动作和目标，生成原子事件（动态组合）
            for action_cn in action_types:
                action_cn = action_cn.strip()
                if action_cn not in action_mapping or action_cn == "未明确":
                    continue
                action_en, action_desc = action_mapping[action_cn]
                
                for target in targets:
                    target = target.strip()
                    if target == "未明确" or not target:
                        continue
                    
                    # 动态判定事件类型
                    event_type = get_event_type(cluster_nums)
                    # 动态生成唯一标识和描述
                    event_id = generate_event_id(cluster_nums, action_en, target)
                    event_desc = generate_event_desc(cluster_nums, action_desc, target)
                    
                    # 去重+存储
                    if event_id and event_desc and event_id not in seen_event_ids:
                        seen_event_ids.add(event_id)
                        if event_type not in atomic_events:
                            atomic_events[event_type] = []
                        atomic_events[event_type].append({
                            "事件标识": event_id,
                            "描述": event_desc
                        })
        except Exception as e:
            print(f"处理第{idx}行结构化数据失败：{e} | 数据：{item}")
            continue
    
    return atomic_events

# ===================== 3. 通用导出函数（适配任意事件类型） =====================
def export_atomic_events_generic(
    atomic_events: Dict[str, List[Dict]], 
    output_file: str = "atomic_events_generic.xlsx"
) -> pd.DataFrame:
    """
    通用导出原子事件为Excel表格（匹配“事件类型+唯一标识+自然语言描述”格式）
    :param atomic_events: 按类型分类的原子事件字典
    :param output_file: 输出Excel文件路径
    :return: 生成的DataFrame
    """
    try:
        # 构造表格数据（仅第一行显示事件类型，后续行留空）
        table_data = []
        for event_type, events in atomic_events.items():
            for event_idx, event in enumerate(events):
                type_display = event_type if event_idx == 0 else ""
                table_data.append({
                    "事件类型": type_display,
                    "标准化原子事件名称（唯一标识）": event["事件标识"],
                    "对应自然语言描述": event["描述"]
                })
        
        # 写入Excel（兼容中文，无索引）
        df = pd.DataFrame(table_data)
        df.to_excel(output_file, index=False, engine="openpyxl", encoding="utf-8")
        print(f"\n✅ 原子事件表格已成功导出至：{output_file}")
        return df
    except Exception as e:
        print(f"导出Excel失败：{e}")
        return pd.DataFrame()

# ===================== 4. 辅助函数：加载结构化拆分结果（JSON文件） =====================
def load_structured_data(file_path: str) -> List[Dict]:
    """
    加载消歧+结构化拆分后的JSON文件
    :param file_path: JSON文件路径
    :return: 结构化数据列表
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            structured_data = json.load(f)
        print(f"✅ 成功加载结构化数据：{file_path} | 数据行数：{len(structured_data)}")
        return structured_data
    except FileNotFoundError:
        print(f"❌ 未找到文件：{file_path}")
        return []
    except json.JSONDecodeError:
        print(f"❌ JSON文件解析失败：{file_path}（请检查文件格式）")
        return []
    except Exception as e:
        print(f"❌ 加载结构化数据失败：{e}")
        return []

# ===================== 5. 主执行流程（可直接运行，适配任意集群） =====================
if __name__ == "__main__":
    # -------------------------- 配置项（根据你的实际场景修改） --------------------------
    STRUCTURED_DATA_PATH = "structured_result.json"  # 消歧+结构化拆分后的JSON文件路径
    ALL_CLUSTER_COUNT = 4  # 总集群数（4/5/6等，根据你的任务修改）
    OUTPUT_EXCEL_PATH = "atomic_events_4cluster.xlsx"  # 输出Excel路径
    
    # -------------------------- 核心执行步骤 --------------------------
    # 步骤1：加载结构化拆分结果（消歧后的JSON）
    structured_data = load_structured_data(STRUCTURED_DATA_PATH)
    if not structured_data:
        # 若加载失败，使用5集群示例数据兜底
        print("\n⚠️  使用5集群示例数据继续执行...")
        structured_data = [
            {
                "问题类型": "动作边界模糊",
                "执行主体": "5号集群",
                "动作类型": "移动",
                "目标点": "hq_mark11",
                "时序逻辑": "前期",
                "原始歧义点": "5号集群飞往hq_mark11未明确动作类型",
                "消歧后表述": "5号集群独立移动（位移）至hq_mark11完成"
            },
            {
                "问题类型": "协同边界模糊",
                "执行主体": "1+3+5号集群",
                "动作类型": "会合",
                "目标点": "hq_5",
                "时序逻辑": "共同",
                "原始歧义点": "1+3+5号集群在hq_5会合表述模糊",
                "消歧后表述": "1号、3号、5号集群在hq_5完成会合（协同）"
            },
            {
                "问题类型": "全集群协同模糊",
                "执行主体": "所有集群",
                "动作类型": "突破",
                "目标点": "hq_mark12",
                "时序逻辑": "最后",
                "原始歧义点": "所有集群突破hq_mark12表述模糊",
                "消歧后表述": "1+2+3+4+5号集群共同突破hq_mark12完成"
            },
            {
                "问题类型": "动作边界模糊",
                "执行主体": "2号集群",
                "动作类型": "突破",
                "目标点": "hq_mark8",
                "时序逻辑": "依次",
                "原始歧义点": "2号集群突破hq_mark8未明确完成状态",
                "消歧后表述": "2号集群独立突破hq_mark8完成"
            }
        ]
    
    # 步骤2：生成通用原子事件（适配ALL_CLUSTER_COUNT指定的集群数）
    atomic_events = generate_atomic_events_generic(structured_data, ALL_CLUSTER_COUNT)
    print(f"\n✅ 原子事件：{json.dumps(atomic_events, ensure_ascii=False, indent=2)}")
    # 步骤3：导出为Excel表格（匹配你需要的格式）
    df = export_atomic_events_generic(atomic_events, OUTPUT_EXCEL_PATH)
    
    # 步骤4：打印生成结果（控制台验证）
    if not df.empty:
        print("\n===== 生成的原子事件列表（前10行） =====")
        print(df.head(10).to_string(index=False, max_colwidth=50)) 