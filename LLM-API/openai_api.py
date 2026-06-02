# 用于测试从OpenAI API获取数据的功能
import os
from openai import OpenAI

client = OpenAI()
k = os.getenv("OPENAI_API_KEY", "")
print("key startswith sk-:", k.startswith("sk-"))
response = client.responses.create(
    model="gpt-5.4",
    input="Write a one-sentence bedtime story about a unicorn."
)
print(response.output_text)
