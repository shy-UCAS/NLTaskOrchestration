import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),  # 建议放环境变量/ .env
    base_url="https://api2.qiandao.mom/v1",
)

resp = client.chat.completions.create(
    model="gemini-3.1-pro-preview-h",
    messages=[{"role": "user", "content": "介绍一下你自己。"}],
    stream=False,  # 你说建议关闭流式
)

print(resp.choices[0].message.content)