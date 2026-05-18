import re
from openai import OpenAI

client = OpenAI(
    base_url="https://www.packyapi.com/v1",
    api_key="sk-a2ng1pUr3hoTWH0gMovetsXMgOYT5UbpS0oITbmGyx3Luiz3"
)

MODEL = "deepseek-v4-pro"

# 初始系统提示
messages = [{"role": "system", "content": "You are a helpful AI assistant"}]

print("【对话开始，输入空行退出】")

while True:
    user_input = input(f"user:").strip()
    if not user_input:
        print("已退出")
        break
    # 把用户消息加入上下文
    messages.append({"role": "user", "content": user_input})
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.6,
            top_p=0.95,
            max_tokens=38912,
            stream=False,
            extra_body={"enable_thinking": False, "top_k": 20}
        )
        msg = response.choices[0].message
        raw_content = msg.content or ""

        # 1. 优先尝试 vLLM 的 reasoning_content 字段
        reasoning = getattr(msg, "reasoning_content", None)
        if not reasoning and raw_content:
            # 2. 若没有，从 content 中解析 <think>...</think> 内容
            match = re.search(r"<think>(.*?)</think>", raw_content, re.DOTALL)
            if match:
                reasoning = match.group(1).strip()
                raw_content = raw_content.replace(match.group(0), "").strip()

        if reasoning:
            print(f"【思考过程】\n{reasoning}\n")
        print(f"assistant: {raw_content}")
        messages.append({"role": "assistant", "content": raw_content})
    except Exception as e:
        print("调用失败：", e)