from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
import json
# 1. 定义消歧示例（可选，提升准确率）
examples = [
    {
        "input": "3号集群飞往hq_mark2，再前往hq_2会合",
        "output": "歧义点：hq_2与hq_mark2是不同目标；消歧后：hq_2=汇聚会合点，hq_mark2=3号集群突破目标点"
    }
]
# 2. 定义消歧提示模板
prompt_template = PromptTemplate(
    input_variables=["input_text"],
    template="""请完成以下消歧任务：
    1. 识别文本中的模糊点（如同名不同目标、动作边界模糊）；
    2. 按“歧义点+消歧后表述”格式输出，每个歧义点单独一行；
    3. 明确动作类型：移动（位移）、突破（作战）、会合（协同）。
    输入文本：{input_text}"""
)
# 3. 封装LLM API调用
llm = ChatOpenAI(
    model="gemini-3.1-pro-preview-h", 
    api_key="sk-CDwQAMCzrYQvhGMy5PBrS6zIuWWoqpBg8FeG0Ka3Y4T8sJRs",
    base_url="https://api2.qiandao.mom/v1"
)
chain = prompt_template | llm | StrOutputParser()
# 4. 调用执行
raw_text = "蓝方兵力分为四个集群，1号集群首先独立进攻hq_mark6，随后飞往hq_mark7与2号集群会合形成大部队，然后一起共同执行突破hq_mark1，然后突破hq_mark5，最后与其他编队汇聚到hq_2后，所有人飞往hq_mark4完成最后的突破。2号集群独立飞往hq_mark7，与1号集群会合后执行同样的后续行动。3号集群独立依次飞往hq_mark9、hq_mark8、hq_mark2、hq_mark4完成突破，随后前往hq_2与1、2、4号集群会合，后续共同行动。4号集群独立飞往hq_mark10、hq_mark3执行突破任务后，汇聚到hq_2与1、3号集群会合后，所有 集群一起飞往hq_mark4完成突破。"
disambiguation_result = chain.invoke({"input_text": raw_text})
print(disambiguation_result)