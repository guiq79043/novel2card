# core/prompts.py
# 所有 Prompt 模板統一管理
#
# 模板分類：
#   EXTRACT_*   → 擷取用（給性價比模型）
#   CARD_*      → 角色卡生成用（給高性能模型）
#   WORLDBOOK_* → 世界書生成用（給高性能模型）
#
# 使用方式：
#   from core.prompts import get_extract_prompt, get_card_prompt, get_worldbook_prompt
#
# 自定義模板：
#   在下方 CUSTOM 區塊填入你的 prompt，然後在 config.yaml 設定對應的 key。

from core.config import get


# ============================================================
#  輸出 Schema 定義（所有模板共用，確保欄位一致）
# ============================================================

# 擷取模組輸出的 JSON schema 說明（嵌入 prompt 中讓模型遵守）
_EXTRACT_SCHEMA = """
請嚴格依照以下 JSON 格式輸出，禁止輸出任何額外文字、說明或 markdown 標記：

{
  "characters": [
    {
      "name": "角色主要名稱（字串）",
      "aliases": ["別名1", "別名2"],
      "appearance": "外貌描述（字串，無則填空字串）",
      "personality": "性格描述（字串，無則填空字串）",
      "speech_pattern": "最具代表性的一段對話或說話方式（字串，無則填空字串）",
      "background": "身份、關係、背景資訊（字串，無則填空字串）"
    }
  ],
  "world_entries": [
    {
      "type": "location | event | faction | rule | item | other",
      "name": "條目名稱（字串）",
      "keywords": ["觸發關鍵字1", "關鍵字2"],
      "content": "詳細說明（字串）"
    }
  ]
}
"""

# 精簡版只要求最低限度的 schema
_EXTRACT_SCHEMA_MINIMAL = """
請嚴格依照以下 JSON 格式輸出，禁止輸出任何額外文字、說明或 markdown 標記：

{
  "characters": [
    {
      "name": "角色主要名稱",
      "aliases": [],
      "appearance": "",
      "personality": "",
      "speech_pattern": "",
      "background": ""
    }
  ],
  "world_entries": []
}
"""


# ============================================================
#  擷取模板（給性價比模型）
# ============================================================

# --- 精簡版：只擷取主要角色的核心資訊，省 token ---
EXTRACT_MINIMAL = """\
你是一位小說角色擷取 AI。
請從以下小說段落中，找出「有名字且多次出現」的主要角色，提取最基本的識別資訊。

規則：
- 只記錄有具體名字的角色，忽略路人、無名配角
- 每個欄位盡量簡短，不需要長篇描述
- 若某欄位在此段落中完全沒有資訊，填入空字串

{schema}

小說段落：
""" .replace("{schema}", _EXTRACT_SCHEMA_MINIMAL)

# --- 標準版：擷取有一定重要性的角色與世界觀資訊 ---
EXTRACT_STANDARD = """\
你是一位專業的小說內容分析 AI。
請從以下小說段落中提取角色與世界觀資料。

角色擷取規則：
- 記錄所有出現的具名角色（含次要角色）
- aliases 填入此段落出現的其他稱呼（綽號、稱謂、別名）
- speech_pattern 填入最能代表該角色說話方式的一句話
- 若某欄位在此段落中無資訊，填入空字串

世界觀擷取規則：
- 記錄重要地點、重大事件、勢力組織、特殊道具或規則
- keywords 填入自然會觸發此條目的關鍵詞（2~4 個為佳）
- 純粹日常的場景描述不需要記錄

{schema}

小說段落：
""".replace("{schema}", _EXTRACT_SCHEMA)

# --- 詳細版：盡可能擷取所有可能有用的資訊 ---
EXTRACT_DETAILED = """\
你是一位極其細心的小說世界建構分析 AI。
請從以下小說段落中，盡可能完整地提取所有角色與世界觀資訊，
包含任何可能對後續角色扮演或世界書有幫助的細節。

角色擷取規則：
- 記錄所有出現的角色，包含只出現一次的配角
- aliases 盡可能列出所有稱呼方式
- appearance 包含服裝、氣質、動作習慣等所有外在描述
- personality 包含細微的情緒反應、價值觀等
- speech_pattern 盡量保留原文對話，體現語氣特徵
- background 記錄所有可推斷的身份、關係、過去經歷

世界觀擷取規則：
- 任何專有名詞、地名、組織、術語、道具都要記錄
- 隱含的規則或設定也要記錄（如魔法系統的細節、社會結構）
- keywords 應包含所有可能觸發此條目的詞彙

{schema}

小說段落：
""".replace("{schema}", _EXTRACT_SCHEMA)

# --- 自定義槽位（在此填入你自己的擷取 prompt）---
# 填寫後在 config.yaml 設定 extract_template: "custom_1" 即可使用

EXTRACT_CUSTOM_1 = """\
# 在此填入你的自定義擷取 prompt
# 提示：結尾必須包含 JSON schema 說明，並以「小說段落：」結尾
# 可以複製上方任一模板修改

{schema}

小說段落：
""".replace("{schema}", _EXTRACT_SCHEMA)

EXTRACT_CUSTOM_2 = """\
# 在此填入你的第二個自定義擷取 prompt

{schema}

小說段落：
""".replace("{schema}", _EXTRACT_SCHEMA)

# --- 單一文件深度版：整本小說作為單一 chunk 時使用 ---
# 告知模型這是完整小說，不需要節省篇幅，每個欄位盡可能詳盡
EXTRACT_SINGLE = """\
你是一位專業的小說角色與世界觀分析 AI。
以下提供的是一部完整的小說全文，請對全文進行深度分析並提取所有資料。

重要前提：
- 這是完整的小說全文，不是片段，你可以看到所有角色的完整發展
- 不需要節省篇幅，每個欄位請盡可能詳盡填寫
- 可以根據全文推斷角色的隱含動機、人際關係變化、成長軌跡

角色擷取規則：
- 記錄所有出現的具名角色（含只出現一次的配角）
- aliases 列出角色在全文中出現的所有稱呼方式
- appearance 詳細描述外貌、服裝、體型、氣質、標誌性動作或習慣，盡可能完整
- personality 詳細描述性格特質、價值觀、行為模式、情緒傾向、對不同人的態度差異，不限字數
- speech_pattern 擷取 7~8 段最能體現角色說話風格的原文對話，涵蓋不同情境
- background 詳細記錄身份、過去經歷、人際關係網絡、在故事中的角色定位、
  動機與目標、以及全文中可推斷的隱含資訊

世界觀擷取規則：
- 任何專有名詞、地名、組織、術語、道具、規則、能力系統都要記錄
- 隱含的世界觀設定也要記錄（從情節可推斷的規則、社會結構、歷史背景）
- content 盡可能詳盡，不限字數
- keywords 包含所有可能觸發此條目的詞彙

{schema}

小說全文：
""".replace("{schema}", _EXTRACT_SCHEMA)


# ============================================================
#  角色卡生成模板（給高性能模型）
# ============================================================

_CARD_SCHEMA = """\
請嚴格依照以下 JSON 格式輸出，禁止輸出任何額外文字、說明或 markdown 標記：

{
  "name": "角色名稱",
  "description": "角色的詳細設定（背景、外貌、身份）",
  "personality": "個性特點、說話風格、行為模式",
  "scenario": "角色所處的世界觀或預設情境",
  "first_mes": "角色的第一句開場白（第一人稱，符合角色性格）",
  "mes_example": "<START>\\n{{user}}: （範例使用者發言）\\n{{char}}: （角色回應，展現說話風格）",
  "creator_notes": "給使用者的備註（角色特別注意事項、扮演建議）",
  "tags": ["標籤1", "標籤2"]
}
"""

# --- 預設版：平衡詳細度與可用性 ---
CARD_DEFAULT = """\
你是一位專業的 SillyTavern 角色卡製作 AI。
請根據以下角色資料，生成一張完整、平衡的角色卡。

要求：
- description 整合所有來源資料，避免重複，約 200~400 字
- personality 條列式描述核心特質，每項一行，約 100~200 字
- scenario 描述適合這個角色的預設情境，約 100 字
- first_mes 符合角色性格，自然且有帶入感，約 50~100 字
- mes_example 展示角色獨特的說話方式
- tags 包含角色性別、性格標籤、小說名稱

{schema}

角色資料：
""".replace("{schema}", _CARD_SCHEMA)

# --- 沉浸版：description 更豐富，適合主角或重要角色 ---
CARD_IMMERSIVE = """\
你是一位擅長創作沉浸式角色的 SillyTavern 角色卡製作 AI。
請根據以下角色資料，生成一張帶有強烈臨場感的角色卡。

要求：
- description 以第三人稱敘事風格撰寫，如同在介紹一位真實存在的人，
  涵蓋外貌、氣質、習慣、過去，約 400~600 字，文字要有畫面感
- personality 以自然段落描述，而非條列式，展現角色的立體性，約 200~300 字
- scenario 打造一個具體且引人入勝的相遇情境，約 150 字
- first_mes 開場白要有情境感，讓使用者立刻感受到角色的存在，約 100~150 字
- mes_example 展示至少兩個情境下的回應，突顯角色的一致性

{schema}

角色資料：
""".replace("{schema}", _CARD_SCHEMA)

# --- 精簡版：快速生成，適合次要角色 ---
CARD_CONCISE = """\
你是一位 SillyTavern 角色卡製作 AI。
請根據以下角色資料，生成一張精簡但完整的角色卡。

要求：
- description 只保留最核心的外貌與背景，約 100~150 字
- personality 3~5 個關鍵詞加簡短說明，約 50~80 字
- scenario 一句話描述預設情境
- first_mes 簡短有力，約 30~50 字
- mes_example 一組對話即可

{schema}

角色資料：
""".replace("{schema}", _CARD_SCHEMA)

# --- 自定義槽位 ---
CARD_CUSTOM_1 = """\
# 在此填入你的自定義角色卡生成 prompt
# 結尾必須包含 JSON schema，並以「角色資料：」結尾

{schema}

角色資料：
""".replace("{schema}", _CARD_SCHEMA)

CARD_CUSTOM_2 = """\
# 在此填入你的第二個自定義角色卡生成 prompt

{schema}

角色資料：
""".replace("{schema}", _CARD_SCHEMA)


# ============================================================
#  世界書生成模板（給高性能模型）
# ============================================================

_WORLDBOOK_SCHEMA = """\
請嚴格依照以下 JSON 格式輸出，禁止輸出任何額外文字、說明或 markdown 標記：

{
  "name": "條目名稱",
  "keywords": ["觸發關鍵字1", "關鍵字2"],
  "content": "注入到上下文的內容（這是角色扮演時 AI 看到的設定）",
  "comment": "給人類閱讀的備註說明"
}
"""

# --- 敘事型：以故事語氣描述，融入氛圍 ---
WORLDBOOK_NARRATIVE = """\
你是一位擅長世界觀建構的創作 AI。
請根據以下從小說中提取的原始資料，生成一個適合 SillyTavern 世界書的條目。

要求：
- content 以第三人稱、現在式的敘事風格撰寫，如同百科但帶有文學性
- 語氣融入小說的世界觀氛圍，避免過於學術乾燥
- content 約 100~250 字，涵蓋此條目的核心資訊
- keywords 選擇自然出現在對話中的詞，2~5 個
- comment 用一句話說明此條目的用途

{schema}

原始資料：
""".replace("{schema}", _WORLDBOOK_SCHEMA)

# --- 資訊型：精確的百科風格，適合規則、系統類設定 ---
WORLDBOOK_informational = """\
你是一位世界書條目編輯 AI。
請根據以下從小說中提取的原始資料，生成一個清晰、精確的 SillyTavern 世界書條目。

要求：
- content 以客觀、精確的方式描述，重點是讓 AI 能快速理解關鍵資訊
- 使用條列式或分段方式呈現，確保資訊密度高
- 避免主觀評價和文學性描述
- content 約 80~150 字
- keywords 選擇精確的專有名詞，2~4 個
- comment 說明此條目適用的觸發情境

{schema}

原始資料：
""".replace("{schema}", _WORLDBOOK_SCHEMA)

# --- 自定義槽位 ---
WORLDBOOK_CUSTOM_1 = """\
# 在此填入你的自定義世界書生成 prompt
# 結尾必須包含 JSON schema，並以「原始資料：」結尾

{schema}

原始資料：
""".replace("{schema}", _WORLDBOOK_SCHEMA)


# ============================================================
#  模板索引（供外部呼叫）
# ============================================================

_EXTRACT_TEMPLATES = {
    "minimal":  EXTRACT_MINIMAL,
    "standard": EXTRACT_STANDARD,
    "detailed": EXTRACT_DETAILED,
    "single":   EXTRACT_SINGLE,   # 整本小說單一 chunk 時使用
    "custom_1": EXTRACT_CUSTOM_1,
    "custom_2": EXTRACT_CUSTOM_2,
}

_CARD_TEMPLATES = {
    "default":  CARD_DEFAULT,
    "immersive": CARD_IMMERSIVE,
    "concise":  CARD_CONCISE,
    "custom_1": CARD_CUSTOM_1,
    "custom_2": CARD_CUSTOM_2,
}

_WORLDBOOK_TEMPLATES = {
    "narrative":     WORLDBOOK_NARRATIVE,
    "informational": WORLDBOOK_informational,
    "custom_1":      WORLDBOOK_CUSTOM_1,
}


# ============================================================
#  全局提示詞注入
# ============================================================

def _build_hint_prefix() -> str:
    """
    讀取 config.yaml 的 global_hint 欄位。
    若非空，回傳格式化後的提示前綴（以醒目分隔線標示最高優先級）。
    若為空，回傳空字串。
    """
    hint = get("global_hint", "") or ""
    hint = hint.strip()
    if not hint:
        return ""
    return (
        "!!!! 最高優先級指示，必須嚴格遵守，優先於以下所有指令 !!!!\n"
        f"{hint}\n"
        "!!!! 最高優先級指示結束 !!!!\n\n"
    )


def get_extract_prompt() -> str:
    """
    依據 config.yaml 的 extract_template 取得擷取 prompt。
    預設使用 standard。若 global_hint 非空，注入於最前方。
    """
    key = get("extract_template", "standard")
    if key not in _EXTRACT_TEMPLATES:
        raise ValueError(
            f"extract_template '{key}' 不存在。\n"
            f"可用選項：{list(_EXTRACT_TEMPLATES.keys())}"
        )
    return _build_hint_prefix() + _EXTRACT_TEMPLATES[key]


def get_card_prompt() -> str:
    """
    依據 config.yaml 的 card_template 取得角色卡生成 prompt。
    預設使用 default。若 global_hint 非空，注入於最前方。
    """
    key = get("card_template", "default")
    if key not in _CARD_TEMPLATES:
        raise ValueError(
            f"card_template '{key}' 不存在。\n"
            f"可用選項：{list(_CARD_TEMPLATES.keys())}"
        )
    return _build_hint_prefix() + _CARD_TEMPLATES[key]


def get_worldbook_prompt() -> str:
    """
    依據 config.yaml 的 worldbook_template 取得世界書生成 prompt。
    預設使用 narrative。若 global_hint 非空，注入於最前方。
    """
    key = get("worldbook_template", "narrative")
    if key not in _WORLDBOOK_TEMPLATES:
        raise ValueError(
            f"worldbook_template '{key}' 不存在。\n"
            f"可用選項：{list(_WORLDBOOK_TEMPLATES.keys())}"
        )
    return _build_hint_prefix() + _WORLDBOOK_TEMPLATES[key]
