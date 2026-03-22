import re
import urllib.parse
import webbrowser

# 因为Spot在Windows环境下原生安装极其困难(强依赖Linux C++17及以上)，
# 我们改用纯Python开发的轻量级验证库 ltlf2dfa (需先执行 pip install ltlf2dfa)
from ltlf2dfa.parser.ltlf import LTLfParser 

ATOM_PROPOSITION = {
    "φ₁": "cluster1_attack_hqmark6_done",
    "φ₂": "cluster1_move_hqmark7_done",
    "φ₃": "cluster2_move_hqmark7_done",
    "φ₄": "cluster1_cluster2_meet_hqmark7_done",
    "φ₅": "cluster1_cluster2_breakthrough_hqmark1_done",
    "φ₆": "cluster1_cluster2_breakthrough_hqmark5_done",
    "φ₇": "cluster1_cluster2_move_hq2_done",
    "φ₈": "cluster3_move_hqmark9_done",
    "φ₉": "cluster3_move_hqmark8_done",
    "φ₁₀": "cluster3_move_hqmark2_done",
    "φ₁₁": "cluster3_breakthrough_hqmark4_done",
    "φ₁₂": "cluster3_move_hq2_done",
    "φ₁₃": "cluster4_move_hqmark10_done",
    "φ₁₄": "cluster4_move_hqmark3_done",
    "φ₁₅": "cluster4_breakthrough_hqmark10_hqmark3_done",
    "φ₁₆": "cluster4_move_hq2_done",
    "φ₁₇": "cluster1_2_3_4_meet_hq2_done",
    "φ₁₈": "cluster1_2_3_4_move_hqmark4_done",
    "φ₁₉": "cluster1_2_3_4_breakthrough_hqmark4_done"
}
# 修正后的时序约束→LTL映射（核心是∧表示并行，□(协同→前置都完成)表示同步）
CORRECTED_TIME_CONSTRAINTS = [
    # 1号集群独立动作（内部顺序，并行于其他集群）
    ("先A后B", "φ₁", "φ₂"),  # 1号：进攻hq6 → 移动hq7（内部顺序，并行执行）
    # 2号集群独立动作（并行于1号）
    ("无前置", "φ₃"),  # 2号：移动hq7（无前置，与1号φ₂并行）
    # 1+2号协同同步点：都完成移动hq7后会合（核心并行+同步）
    ("协同同步", "φ₂ ∧ φ₃", "φ₄"),  # □(φ₄ → (φ₂ ∧ φ₃)) + ◇(φ₂ ∧ φ₃)
    # 1+2号会合后内部顺序（并行于3、4号）
    ("先A后B", "φ₄", "φ₅"), ("先A后B", "φ₅", "φ₆"), ("先A后B", "φ₆", "φ₇"),
    # 3号集群独立动作（全程并行于1、2、4号）
    ("顺序并行", "φ₈", "φ₉"), ("顺序并行", "φ₉", "φ₁₀"), ("顺序并行", "φ₁₀", "φ₁₁"), ("顺序并行", "φ₁₁", "φ₁₂"),
    # 4号集群独立动作（全程并行于1、2、3号）
    ("顺序并行", "φ₁₃", "φ₁₄"), ("顺序并行", "φ₁₄", "φ₁₅"), ("顺序并行", "φ₁₅", "φ₁₆"),
    # 最终协同同步点：所有集群都到hq2后会合（全局同步）
    ("全局同步", "φ₇ ∧ φ₁₂ ∧ φ₁₆", "φ₁₇"),  # □(φ₁₇ → (φ₇ ∧ φ₁₂ ∧ φ₁₆))
    # 最终突破（同步后顺序）
    ("先A后B", "φ₁₇", "φ₁₈"), ("先A后B", "φ₁₈", "φ₁₉"),
    # 所有集群独立动作最终都完成（并行完成）
    ("并行完成", "φ₂", "φ₃", "φ₈", "φ₁₃"),  # ◇φ₂ ∧ ◇φ₃ ∧ ◇φ₈ ∧ ◇φ₁₃（4个集群独立动作并行完成）
]

# 修正后的LTL算符映射（新增并行/同步规则）
CORRECTED_LTL_OPERATOR_MAP = {
    "先A后B": lambda a, b: f"({a} U {b})",  # 集群内部顺序
    "无前置": lambda a: f"(◇{a})",  # 无前置，并行执行
    "协同同步": lambda a, b: f"(□({b} → {a}) ∧ ◇{a})",  # 同步点：b仅在a都完成后触发，且a最终并行完成
    "顺序并行": lambda a, b: f"(◇{a} ∧ ◇{b} ∧ ({a} U {b}))",  # 集群内部顺序，外部并行
    "全局同步": lambda a, b: f"(□({b} → {a}) ∧ ◇{a})",  # 全局协同同步
    "并行完成": lambda *args: f"({' ∧ '.join([f'◇{arg}' for arg in args])})",  # 多集群动作并行完成
}

# 生成修正后的完整LTL公式
def generate_parallel_ltl(constraints, atom_map, operator_map):
    ltl_fragments = []
    for constraint_type, *args in constraints:
        actual_args = [atom_map[arg.strip()] if arg.strip() in atom_map else arg for arg in args]
        ltl_fragment = operator_map[constraint_type](*actual_args)
        ltl_fragments.append(ltl_fragment)
    return f"({' ∧ '.join(ltl_fragments)})"

def convert_to_ascii_syntax(ltl_str):
    """
    将中文或人类阅读友好的 Unicode LTL 符号转换为标准 ASCII 语法
    """
    replacements = {
        "◇": "F ",
        "□": "G ",
        "∧": " & ",
        "∨": " | ",
        "→": " -> ",
        "U": " U "
    }
    parsed_str = ltl_str
    for old, new in replacements.items():
        parsed_str = parsed_str.replace(old, new)
        
    # 为了满足语法解析器对变量命名的要求：把 φ₁ 统一转为 phi_1 格式
    def replace_subscript(match):
        sub_map = {"₁":"1","₂":"2","₃":"3","₄":"4","₅":"5","₆":"6","₇":"7","₈":"8","₉":"9","₀":"0"}
        val = match.group(0)
        num = "".join([sub_map[c] for c in val[1:]])
        return f"phi_{num}"
        
    parsed_str = re.sub(r'φ[₁₂₃₄₅₆₇₈₉₀]+', replace_subscript, parsed_str)
    return parsed_str

def validate_ltl_syntax(ltl_formula):
    """校验LTL公式语法合法性"""
    ascii_formula = convert_to_ascii_syntax(ltl_formula)
    try:
        # ltlf2dfa 提供的 LTL 句法检查规则与标准 LTL 完全一致
        parser = LTLfParser()
        f = parser(ascii_formula)
        print(f"\n✅ 语法校验通过！")
        print(f"转换后的机器语言: {ascii_formula}")
        print(f"解析器生成的树形结构: {f}")
        return True, ascii_formula
    except Exception as e:
        print(f"\n❌ 语法错误！")
        print(e)
        return False, str(e)

def visualize_tgba_online(ascii_formula):
    """
    将标准 ASCII LTL 公式一键发送至官方的 Spot Web App 以生成 TGBA 自动机。
    这巧妙绕过了 Windows 环境下编译 Spot C++ 库的困难。
    """
    print("\n⏳ 正在准备广义 Büchi 自动机 (TGBA) 数据...")
    
    encoded_formula = urllib.parse.quote(ascii_formula)
    # 使用最新的 Spot web 路由与页面锚点:  #!/?f=
    url = f"https://spot.lrde.epita.fr/app/#!/?f={encoded_formula}"
    
    print("="*60)
    print("📋 [手动复制专用] 请将以下纯英文转换后的公式复制并粘贴到网站的顶部输入框中：\n")
    print(ascii_formula)
    print("\n" + "="*60)
    
    print(f"\n🔗 网站直达链接: {url}")
    print(f"   (系统应已自动在您的默认浏览器中打开该页面)")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"无法自动打开浏览器，请手动复制上述链接访问。")

# 执行校验


# 执行生成
corrected_ltl = generate_parallel_ltl(CORRECTED_TIME_CONSTRAINTS, ATOM_PROPOSITION, CORRECTED_LTL_OPERATOR_MAP)
print("修正后的并行+协同LTL公式：")
print(corrected_ltl)
is_valid, final_formula = validate_ltl_syntax(corrected_ltl)
print(f"公式合法性: {is_valid}")

if is_valid:
    visualize_tgba_online(final_formula)
