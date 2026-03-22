'''结构化解析消歧结果'''
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
import re
import json
import pandas as pd

# ===================== 1. 原有消歧逻辑（复用） =====================
def get_disambiguation_result(raw_text):
    # 定义消歧提示模板
    prompt_template = PromptTemplate(
        input_variables=["input_text"],
        template="""请完成以下消歧任务：
        1. 识别文本中的模糊点（如同名不同目标、动作边界模糊）；
        2. 按“歧义点：XXX + 消歧后表述：XXX”格式输出，每个歧义点单独一行；
        3. 明确动作类型：移动（位移）、突破（作战）、会合（协同）。
        输入文本：{input_text}"""
    )
    # 封装LLM API调用
    llm = ChatOpenAI(
        model="gemini-3.1-pro-preview-h", 
        api_key="sk-CDwQAMCzrYQvhGMy5PBrS6zIuWWoqpBg8FeG0Ka3Y4T8sJRs",
        base_url="https://api2.qiandao.mom/v1"
    )
    chain = prompt_template | llm | StrOutputParser()
    # 执行消歧
    disambiguation_result = chain.invoke({"input_text": raw_text})
    return disambiguation_result

# ===================== 2. 新增：结构化解析消歧结果 =====================
def parse_disambiguation_to_structured(disambig_result):
    """
    解析消歧文本，提取结构化字段：
    - 问题类型（如动作边界模糊、时序模糊）
    - 执行主体（如1号集群、1/2/3/4号集群）
    - 动作类型（移动/突破/会合）
    - 目标点（如hq_mark6、hq_2）
    - 时序逻辑（如前期、依次、随后、最后）
    - 原始歧义点描述
    - 消歧后完整表述
    """
    # 放弃严格正则匹配，改用分隔符切块以适应大模型不同的输出排版格式（如序号、加粗、换行、有无加号等）
    blocks = re.split(r"(?:\n|^)\s*(?:\d+[\.、]\s*)?\*?\*?歧义点[:：]", disambig_result)
    
    structured_data = []
    # 定义规则：提取各类结构化字段的正则/关键词
    action_keywords = {
        "移动（位移）": "移动|位移|前往|飞往|到达",
        "突破（作战）": "突破|作战|进攻|独立进攻",
        "会合（协同）": "会合|协同|汇聚|集结"
    }
    time_keywords = ["前期", "依次", "随后", "最后", "共同", "首先", "完成后", "先行", "再次"]
    target_pattern = r"hq[_a-zA-Z0-9]+"  # 匹配hq_mark6、hq_2等目标点

    for block in blocks:
        if "消歧后表述" not in block:
            continue
        
        # 按“消歧后表述”切分，分离出问题描述和消歧内容
        parts = re.split(r"消歧后表述[:：]", block)
        if len(parts) >= 2:
            # 清理格式符号（如首尾的加号、星号、空格等）
            ambiguity_desc = re.sub(r"[\+\s\*]+$", "", parts[0]).strip()
            ambiguity_desc = re.sub(r"^\*+", "", ambiguity_desc).strip()
            
            disambig_desc = "".join(parts[1:])
            disambig_desc = re.sub(r"^[ \n\*]+", "", disambig_desc)
            disambig_desc = re.sub(r"[\s\*]+$", "", disambig_desc).strip()

            # 步骤1：提取问题类型（括号内的标签或其他描述）
            problem_type = re.findall(r"（(.*?)）", ambiguity_desc)
            if not problem_type: # 有的大模型不输出括号，输出在文字中
                if "动作边界" in ambiguity_desc: problem_type = ["动作边界"]
                elif "属性" in ambiguity_desc: problem_type = ["动作属性"]
                elif "辨析" in ambiguity_desc: problem_type = ["目标辨析"]
            problem_type = "|".join(problem_type) if problem_type else "未标注"
            
            # 步骤2：提取执行主体（集群编号）
            subject_pattern = r"(\d号集群|\d、\d号集群|\d/\d号集群|四个集群|全部集群|全集群|所有集群)"
            subjects = re.findall(subject_pattern, disambig_desc)
            subject = "|".join(set(subjects)) if subjects else "未明确"
            
            # 步骤3：提取动作类型
            actions = []
            for action_name, action_re in action_keywords.items():
                if re.search(action_re, disambig_desc):
                    actions.append(action_name.split("（")[0])
            action_type = "|".join(actions) if actions else "未明确"
            
            # 步骤4：提取目标点
            targets = re.findall(target_pattern, disambig_desc)
            target_point = "|".join(set(targets)) if targets else "未明确"
            
            # 步骤5：提取时序逻辑
            time_logic = [tk for tk in time_keywords if tk in disambig_desc]
            time_logic = "|".join(time_logic) if time_logic else "未明确"
            
            # 封装结构化数据
            structured_data.append({
                "问题类型": problem_type,
                "执行主体": subject,
                "动作类型": action_type,
                "目标点": target_point,
                "时序逻辑": time_logic,
                "原始歧义点": ambiguity_desc,
                "消歧后表述": disambig_desc
            })
    return structured_data

# ===================== 3. 新增：结构化数据输出（JSON/CSV） =====================
def save_structured_data(structured_data, output_format="json", file_path="structured_result"):
    """
    保存结构化数据：
    - output_format: json/csv
    - file_path: 输出文件路径（无需后缀）
    """
    if output_format == "json":
        with open(f"{file_path}.json", "w", encoding="utf-8") as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=4)
        print(f"JSON格式结构化数据已保存至：{file_path}.json")
    elif output_format == "csv":
        df = pd.DataFrame(structured_data)
        df.to_csv(f"{file_path}.csv", index=False, encoding="utf-8-sig")
        print(f"CSV格式结构化数据已保存至：{file_path}.csv")
    else:
        raise ValueError("仅支持json/csv格式输出")

# ===================== 4. 主执行流程 =====================
if __name__ == "__main__":
    # 原始模糊文本
    raw_text = """蓝方兵力分为四个集群，1号集群首先独立进攻hq_mark6，随后飞往hq_mark7与2号集群会合形成大部队，然后一起共同执行突破hq_mark1，然后突破hq_mark5，最后与其他编队汇聚到hq_2后，所有人飞往hq_mark4完成最后的突破。2号集群独立飞往hq_mark7，与1号集群会合后执行同样的后续行动。3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破，随后前往hq_2与1、2、4号集群会合，后续共同行动。4号集群独立飞往hq_mark10、hq_mark3执行突破任务后，汇聚到hq_2与1、3号集群会合后，所有 集群一起飞往hq_mark4完成突破。"""
    
    # 步骤1：获取消歧结果
    disambig_result = get_disambiguation_result(raw_text)
    print("===== 消歧原始输出 =====")
    print(disambig_result)
    
    # 步骤2：解析为结构化数据
    structured_data = parse_disambiguation_to_structured(disambig_result)
    print("\n===== 结构化解析结果 =====")
    print(json.dumps(structured_data, ensure_ascii=False, indent=4))
    
    # 步骤3：保存结构化数据（支持JSON/CSV）
    save_structured_data(structured_data, output_format="json")
    save_structured_data(structured_data, output_format="csv")