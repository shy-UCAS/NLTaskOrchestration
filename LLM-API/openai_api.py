# 用于测试从OpenAI API获取数据的功能
import os
from openai import OpenAI

client = OpenAI()
k = os.getenv("OPENAI_API_KEY", "")
print("key startswith sk-:", k)
response = client.responses.create(
    model="gpt-5.4",
    input="Write a one-sentence bedtime story about a unicorn."
)
# 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
print(response.output_text)