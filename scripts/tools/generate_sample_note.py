"""
scripts/tools/generate_sample_note.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()

from agents.Goal_Decomposer.goal_decomposer import _get_domain_system_prompt
from orchestrator.llm import get_deep_reasoning_llm

llm = get_deep_reasoning_llm(temperature=0.2)

# Test 1: Python DSA for 500+ LeetCode C++ engineer
user_instructions = "i have solved 500+ problems on leetcode but in c++, need revision for python dsa"
sys_prompt = _get_domain_system_prompt("Python Data Structures and Algorithms (DSA)", "4-Day Interview Preparation", user_instructions)

user_prompt = (
    "Topic Title: Python Data Structures and Algorithms (DSA)\n"
    "Overall Roadmap: 4-Day Interview Preparation\n"
    "Key Scope to Cover: Lists, Tuples, Dicts, Sets, heapq, collections, time and space complexity\n"
    "LEARNER BACKGROUND & CONTEXT:\n"
    "- User Goal: Solved 500+ LeetCode in C++, revision for Python DSA interview\n"
)

res = llm.invoke([
    {"role": "system", "content": sys_prompt},
    {"role": "user", "content": user_prompt},
])

content = res.content if hasattr(res, "content") else str(res)

print("============================================================")
print("📄 ADAPTIVE TUTORIAL NOTE FOR PYTHON DSA:")
print("============================================================")
print(content)
print("============================================================")
