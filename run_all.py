# run_all.py
# novel2card 主入口
#
# 互動式選單，輸入字母後按 Enter 選擇要執行的步驟。
# 完全相容純指令環境（不依賴任何 GUI 或特殊終端機功能）。
#
# 使用方式：
#   python run_all.py

import sys
import importlib
from pathlib import Path


# ============================================================
#  顏色輸出（自動偵測是否支援，不支援則退回純文字）
# ============================================================

def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_USE_COLOR = _supports_color()


def _c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t):  return _c(t, "32")
def yellow(t): return _c(t, "33")
def cyan(t):   return _c(t, "36")
def bold(t):   return _c(t, "1")
def dim(t):    return _c(t, "2")
def red(t):    return _c(t, "31")


# ============================================================
#  選單定義
# ============================================================

MAIN_MENU = [
    # (選項字母, 顯示名稱, 說明, module_path 或 callable_key)
    ("a", "完整流程（角色卡）",
     "01 分割 → 02a 擷取角色 → 03 整合 → 04 生成角色卡",
     "full_char"),

    ("b", "完整流程（角色卡 + 世界書）",
     "01 → 02a → 02b → 03 → 04 → 05 全部執行",
     "full_all"),

    ("c", "單獨執行世界書流程",
     "02b 擷取世界書 → 05 生成世界書（需已執行過 01）",
     "worldbook_only"),

    ("d", "分步執行",
     "手動選擇要執行哪些步驟",
     "step_select"),

    ("s", "從零開始設定",
     "逐步引導填寫 config.yaml 的所有主要設定",
     "setup"),

    ("i", "環境檢查",
     "檢查套件、小說檔案、config.yaml 是否就緒",
     "init"),

    ("q", "離開", "", "quit"),
]

STEPS = [
    # (選項字母, 顯示名稱, 說明, module_path)
    ("1", "01 分割小說",
     "將 a.txt 切分成多個 chunk",
     "pipeline.01_split_novel"),

    ("2", "02a 擷取角色資料",
     "逐 chunk 送給性價比模型擷取角色",
     "pipeline.02a_extract_characters"),

    ("3", "02b 擷取世界書資料",
     "逐 chunk 送給性價比模型擷取世界觀（可選）",
     "pipeline.02b_extract_worldbook"),

    ("4", "03 整合角色",
     "別名合併、次要角色篩選",
     "pipeline.03_merge_roles"),

    ("5", "04 生成角色卡",
     "使用高性能模型生成 chara_card_v3",
     "pipeline.04_create_cards"),

    ("6", "05 生成世界書",
     "使用高性能模型生成 SillyTavern lorebook",
     "pipeline.05_create_worldbook"),
]

# 預設流程組合
FLOW_FULL_CHAR    = ["1", "2", "4", "5"]
FLOW_FULL_ALL     = ["1", "2", "3", "4", "5", "6"]
FLOW_WORLDBOOK    = ["3", "6"]


# ============================================================
#  輸入工具
# ============================================================

def ask(prompt: str, valid: list[str]) -> str:
    """
    顯示提示，等待使用者輸入合法選項後按 Enter。
    不區分大小寫。
    """
    valid_lower = [v.lower() for v in valid]
    while True:
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "q"
        if ans in valid_lower:
            return ans
        print(f"  請輸入以下選項之一：{' / '.join(valid)}")


def ask_multi(prompt: str, valid: list[str]) -> list[str]:
    """
    允許一次輸入多個選項（如「1 3 5」或「135」），
    回傳合法選項的 list，保持輸入順序。
    """
    valid_lower = [v.lower() for v in valid]
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return []

        # 支援空格分隔或連續輸入
        tokens = raw.replace(",", " ").split() if " " in raw or "," in raw \
            else list(raw)

        chosen = []
        invalid = []
        for t in tokens:
            if t in valid_lower and t not in chosen:
                chosen.append(t)
            elif t not in valid_lower:
                invalid.append(t)

        if invalid:
            print(f"  無效選項：{' '.join(invalid)}，請重新輸入")
            continue
        if not chosen:
            print("  請至少選擇一個步驟")
            continue
        return chosen


def pause():
    """執行完成後暫停，等使用者按 Enter 返回選單"""
    try:
        input(dim("\n  按 Enter 返回主選單..."))
    except (EOFError, KeyboardInterrupt):
        pass


# ============================================================
#  顯示工具
# ============================================================

def print_header():
    print()
    print(bold("=" * 54))
    print(bold(f"  novel2card  ─  小說 → SillyTavern 角色卡生成工具"))
    print(bold("=" * 54))
    print()


def print_main_menu():
    print(cyan("  主選單"))
    print(dim("  " + "-" * 40))
    for key, name, desc, _ in MAIN_MENU:
        if key == "q":
            print()
        line = f"  [{bold(key)}] {name}"
        print(line)
        if desc:
            print(dim(f"       {desc}"))
    print()


def print_step_menu():
    print(cyan("  選擇要執行的步驟"))
    print(dim("  可輸入多個步驟，以空格或逗號分隔（例如：1 2 4 5）"))
    print(dim("  " + "-" * 40))
    for key, name, desc, _ in STEPS:
        print(f"  [{bold(key)}] {name}")
        if desc:
            print(dim(f"       {desc}"))
    print()


def print_flow_preview(step_keys: list[str]):
    """顯示即將執行的步驟清單"""
    step_map = {s[0]: s[1] for s in STEPS}
    print()
    print(cyan("  即將執行的步驟："))
    for i, k in enumerate(step_keys, 1):
        name = step_map.get(k, k)
        print(f"    {i}. {name}")
    print()


# ============================================================
#  模組執行
# ============================================================

def run_step(step_key: str) -> bool:
    """
    執行單一步驟。
    動態 import 對應模組並呼叫 main()。
    回傳 True 代表成功。
    """
    step_map = {s[0]: (s[1], s[3]) for s in STEPS}
    if step_key not in step_map:
        print(red(f"  未知步驟：{step_key}"))
        return False

    name, module_path = step_map[step_key]
    print()
    print(bold(f"{'─'*54}"))
    print(bold(f"  執行：{name}"))
    print(bold(f"{'─'*54}"))

    try:
        # 每次執行前強制清除 config 快取，確保讀到最新的 config.yaml
        # 不清除的話，同一個 Python 進程內修改 config.yaml 不會生效
        import core.config as _cfg_mod
        _cfg_mod._config_cache = None
        # 同樣清除 prompts 的模組快取，確保模板選擇讀到最新設定
        import sys as _sys
        for _mod_name in list(_sys.modules.keys()):
            if _mod_name.startswith("core.") or _mod_name.startswith("pipeline."):
                del _sys.modules[_mod_name]

        # 處理模組名稱含數字前綴的情況（如 pipeline.01_split_novel）
        mod = importlib.import_module(module_path)
        mod.main()
        print()
        print(green(f"  ✅ {name} 完成"))
        return True
    except KeyboardInterrupt:
        print()
        print(yellow(f"  ⚠️  {name} 被使用者中斷"))
        return False
    except Exception as e:
        print()
        print(red(f"  ❌ {name} 執行失敗：{e}"))
        import traceback
        traceback.print_exc()
        return False


def run_steps(step_keys: list[str]):
    """依序執行多個步驟，任一步驟失敗時詢問是否繼續。"""
    total   = len(step_keys)
    success = 0
    failed  = []

    for i, key in enumerate(step_keys, 1):
        step_name = {s[0]: s[1] for s in STEPS}.get(key, key)
        print(dim(f"\n  步驟 {i}/{total}"))

        ok = run_step(key)
        if ok:
            success += 1
        else:
            failed.append(step_name)
            if i < total:
                print()
                ans = ask(
                    yellow(f"  步驟失敗，是否繼續執行剩餘步驟？[y/n]："),
                    ["y", "n"],
                )
                if ans == "n":
                    print(yellow("  已中止流程"))
                    break

    print()
    print(bold("─" * 54))
    print(f"  完成：{success}/{total} 個步驟")
    if failed:
        print(red(f"  失敗：{', '.join(failed)}"))
    print(bold("─" * 54))



# ============================================================
#  從零開始設定引導
# ============================================================

# 模板說明（供引導時顯示）
_EXTRACT_TEMPLATE_DESC = {
    "minimal":  "只提取主要角色核心資訊，最省 token",
    "standard": "標準提取，適合多 chunk 長篇小說（推薦）",
    "detailed": "詳細提取，適合多 chunk 但想要更多細節",
    "single":   "整本小說單一 chunk 專用，深度分析全文（短篇推薦）",
    "custom_1": "自定義模板 1（在 core/prompts.py 填入）",
    "custom_2": "自定義模板 2（在 core/prompts.py 填入）",
}
_CARD_TEMPLATE_DESC = {
    "default":  "平衡詳細度與可用性（推薦）",
    "immersive":"沉浸感優先，description 更豐富，適合主角",
    "concise":  "精簡風格，適合快速生成次要角色",
    "custom_1": "自定義模板 1（在 core/prompts.py 填入）",
    "custom_2": "自定義模板 2（在 core/prompts.py 填入）",
}
_WORLDBOOK_TEMPLATE_DESC = {
    "narrative":     "敘事風格，帶有文學性，融入小說氛圍（推薦）",
    "informational": "資訊風格，精確的百科式描述，適合規則類設定",
    "custom_1":      "自定義模板 1（在 core/prompts.py 填入）",
}
_WORLDBOOK_TYPES = {
    "location": "地點",
    "event":    "事件",
    "faction":  "勢力",
    "rule":     "規則",
    "item":     "道具",
    "other":    "其他",
}


def _ask_input(prompt: str, default: str = "") -> str:
    """顯示提示並等待輸入，直接按 Enter 使用預設值"""
    if default:
        full_prompt = f"  {prompt} [{dim(default)}]：" if _USE_COLOR else f"  {prompt} [{default}]："
    else:
        full_prompt = f"  {prompt}："
    try:
        val = input(full_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val if val else default


def _ask_choice(prompt: str, options: dict, default: str = "") -> str:
    """列出選項讓用戶選擇，回傳選中的 key"""
    print(f"  {prompt}")
    keys = list(options.keys())
    for i, (k, desc) in enumerate(options.items(), 1):
        marker = green("*") if k == default else " "
        print(f"  {marker} [{bold(str(i))}] {k}  {dim(desc)}")
    print(dim(f"  （直接按 Enter 使用標 * 的預設值）"))
    valid = [str(i) for i in range(1, len(keys) + 1)]
    while True:
        try:
            raw = input("  請選擇：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not raw and default:
            return default
        if raw in valid:
            return keys[int(raw) - 1]
        print(f"  請輸入 1~{len(keys)} 之間的數字")


def _ask_bool(prompt: str, default: bool) -> bool:
    """詢問 yes/no，回傳 bool"""
    default_str = "y" if default else "n"
    ans = ask(f"  {prompt} [y/n]（預設 {default_str}）：", ["y", "n", ""])
    if ans == "":
        return default
    return ans == "y"


def _ask_int(prompt: str, default: int, min_val: int = 0, max_val: int = 999999) -> int:
    """詢問整數輸入"""
    while True:
        raw = _ask_input(prompt, str(default))
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
            print(f"  請輸入 {min_val}~{max_val} 之間的數字")
        except ValueError:
            print("  請輸入整數")


def _ask_multiselect(prompt: str, options: dict, default: list) -> list:
    """多選題，回傳選中的 key list"""
    keys = list(options.keys())
    print(f"  {prompt}")
    print(dim("  輸入編號選擇/取消，多個以空格分隔，直接 Enter 完成"))
    selected = set(default)
    while True:
        print()
        for i, (k, label) in enumerate(options.items(), 1):
            mark = green("[✓]") if k in selected else dim("[ ]")
            print(f"    {mark} [{bold(str(i))}] {k}（{label}）")
        print()
        try:
            raw = input("  選擇編號（Enter 完成）：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            break
        tokens = raw.replace(",", " ").split()
        valid_range = [str(i) for i in range(1, len(keys) + 1)]
        for t in tokens:
            if t in valid_range:
                k = keys[int(t) - 1]
                if k in selected:
                    selected.discard(k)
                else:
                    selected.add(k)
    return [k for k in keys if k in selected]


def run_setup():
    """從零開始引導設定 config.yaml 的所有主要選項"""
    import yaml

    config_path = Path("config.yaml")
    if not config_path.exists():
        print()
        print(red("  找不到 config.yaml，請確認檔案存在"))
        pause()
        return

    print()
    print(bold("  從零開始設定"))
    print(dim("  直接按 Enter 使用 [ ] 內的預設值"))
    print(dim("  " + "─" * 44))

    cfg = {}

    # ── [1/8] 基本設定 ─────────────────────────────────────
    print()
    print(cyan("  [1/8] 基本設定"))
    cfg["novel_title"] = _ask_input("小說名稱", "未命名小說")
    cfg["input_file"]  = _ask_input("小說檔案路徑", "a.txt")
    cfg["debug_mode"]  = False  # 靜默設為 false，進階用戶自己改

    # ── [2/8] 性價比模型（擷取用）─────────────────────────
    print()
    print(cyan("  [2/8] 性價比模型（擷取角色和世界書用）"))
    print(dim("  推薦：deepseek-v4-flash / gemini-3.1-flash / claude-haiku"))
    cfg["api_base"] = _ask_input("API Base URL", "https://api.deepseek.com/v1")
    cfg["api_key"]  = _ask_input("API Key")
    cfg["model"]    = _ask_input("模型名稱", "deepseek-v4-flash")

    # ── [3/8] 高性能模型（生成用）─────────────────────────
    print()
    print(cyan("  [3/8] 高性能模型（生成角色卡和世界書用，也用於修復 JSON）"))
    print(dim("  推薦：deepseek-v4-pro / gemini-3.1-pro-preview / claude-sonnet"))
    same = _ask_bool("與性價比模型使用同一個 API？", False)
    if same:
        cfg["pro_api_base"] = cfg["api_base"]
        cfg["pro_api_key"]  = cfg["api_key"]
        cfg["pro_model"]    = _ask_input("高性能模型名稱", "deepseek-v4-pro")
    else:
        cfg["pro_api_base"] = _ask_input("API Base URL", "https://api.deepseek.com/v1")
        cfg["pro_api_key"]  = _ask_input("API Key")
        cfg["pro_model"]    = _ask_input("模型名稱", "deepseek-v4-pro")

    # ── [4/8] 分段設定 ─────────────────────────────────────
    print()
    print(cyan("  [4/8] 分段設定"))
    print(dim("  建議設為模型上下文上限減 1000~2000"))
    print(dim("  短篇整本一次處理：設 200000 以上，再搭配 single 擷取模板"))
    cfg["max_chunk_chars"] = _ask_int("每段最大字元數", 32767, 1000, 2000000)

    # ── [5/8] 模板選擇 ─────────────────────────────────────
    print()
    print(cyan("  [5/8] 模板選擇"))

    print()
    cfg["extract_template"] = _ask_choice(
        "擷取模板（控制從小說中提取多少資訊）：",
        _EXTRACT_TEMPLATE_DESC,
        default="standard",
    )
    print()
    cfg["card_template"] = _ask_choice(
        "角色卡生成模板（控制角色卡的風格）：",
        _CARD_TEMPLATE_DESC,
        default="default",
    )
    print()
    cfg["worldbook_template"] = _ask_choice(
        "世界書生成模板（控制世界書條目的寫作風格）：",
        _WORLDBOOK_TEMPLATE_DESC,
        default="narrative",
    )

    # ── [6/8] 角色合併設定 ─────────────────────────────────
    print()
    print(cyan("  [6/8] 角色合併設定"))

    cfg["alias_merge"] = _ask_bool("啟用別名自動合併（讓模型判斷哪些名字是同一個角色）", True)
    if cfg["alias_merge"]:
        cfg["alias_merge_model"] = _ask_choice(
            "別名判斷使用哪個模型：",
            {
                "extract": "性價比模型（省錢，預設）",
                "analyze": "高性能模型（更準確，費用較高）",
            },
            default="extract",
        )
        cfg["alias_merge_batch_size"] = _ask_int("每批最大名字數量", 50, 10, 200)
    else:
        cfg["alias_merge_model"]      = "extract"
        cfg["alias_merge_batch_size"] = 50

    print()
    print(dim("  次要角色門檻：出現次數低於此值的角色不自動生成卡片"))
    print(dim("  ⚠️  整本小說只有一個 chunk 時，建議設為 0 或 1"))
    cfg["minor_role_threshold"] = _ask_int("次要角色門檻（0 = 停用篩選）", 3, 0, 100)

    # ── [7/8] 世界書設定 ───────────────────────────────────
    print()
    print(cyan("  [7/8] 世界書設定"))

    print()
    wb_whitelist = _ask_multiselect(
        "要生成哪些類型的世界書條目？（全不選 = 生成全部）",
        _WORLDBOOK_TYPES,
        default=[],
    )
    cfg["worldbook_type_whitelist"] = wb_whitelist

    cfg["worldbook_insertion_order"] = {
        "rule": 10, "item": 20, "faction": 30,
        "location": 40, "event": 50, "other": 60,
    }

    # ── [8/8] 其他設定 ─────────────────────────────────────
    print()
    print(cyan("  [8/8] 其他設定"))

    cfg["api_sleep_interval"] = _ask_int(
        "API 呼叫間隔秒數（免費額度建議 5~10，付費 1~2）", 2, 0, 60
    )
    cfg["force_overwrite"] = _ask_bool(
        "強制重新生成已存在的角色卡（修改模板後想刷新全部時開啟）", False
    )
    cfg["card_creator"]  = _ask_input("角色卡 creator 欄位", "novel2card")
    cfg["worldbook_name"] = _ask_input("世界書名稱（留空自動使用小說名稱）", "")
    cfg["retry_limit"]   = _ask_int("API 失敗最大重試次數", 3, 1, 10)

    # Think 模式：直接設 auto，進階用戶自己改
    cfg["extract_thinking_mode"] = "auto"
    cfg["analyze_thinking_mode"] = "auto"

    # global_hint
    print()
    print(dim("  全局提示詞注入（適合結構特殊的小說，留空則跳過）"))
    print(dim("  ⚠️  只支援單行輸入，若需要多行請之後直接編輯 config.yaml"))
    print(dim("  範例：本小說使用第一人稱敘事，「我」指的是主角林夜"))
    cfg["global_hint"] = _ask_input("global_hint", "")

    # ── 預覽確認 ───────────────────────────────────────────
    print()
    print(bold("  " + "─" * 44))
    print(bold("  設定預覽"))
    print(bold("  " + "─" * 44))

    preview_items = [
        ("小說名稱",         cfg["novel_title"]),
        ("小說檔案",         cfg["input_file"]),
        ("性價比模型",       f"{cfg['model']}  （{cfg['api_base']}）"),
        ("高性能模型",       f"{cfg['pro_model']}  （{cfg['pro_api_base']}）"),
        ("每段最大字元",      str(cfg["max_chunk_chars"])),
        ("擷取模板",         cfg["extract_template"]),
        ("角色卡模板",       cfg["card_template"]),
        ("世界書模板",       cfg["worldbook_template"]),
        ("別名合併",         "開啟" if cfg["alias_merge"] else "關閉"),
        ("次要角色門檻",      str(cfg["minor_role_threshold"])),
        ("世界書類型",       "全部" if not cfg["worldbook_type_whitelist"] else "、".join(cfg["worldbook_type_whitelist"])),
        ("API 間隔",         f"{cfg['api_sleep_interval']} 秒"),
        ("強制覆蓋",         "是" if cfg["force_overwrite"] else "否"),
        ("全局提示詞",       cfg["global_hint"] if cfg["global_hint"] else "（無）"),
    ]

    for label, val in preview_items:
        print(f"    {cyan(label):<16} {val}")

    print()
    ans = ask("  確認寫入 config.yaml？[y/n]：", ["y", "n"])
    if ans != "y":
        print(yellow("  已取消，config.yaml 未修改"))
        pause()
        return

    # ── 寫入 config.yaml ───────────────────────────────────
    # 讀取原始 config 保留路徑設定區塊，只覆蓋用戶設定的部分
    with open(config_path, "r", encoding="utf-8") as f:
        original = yaml.safe_load(f)

    # 保留路徑設定（不在引導中修改）
    path_keys = [
        "chunk_output_dir", "mapping_file",
        "char_response_dir", "char_raw_dir", "char_bad_dir", "char_progress_file",
        "wb_response_dir", "wb_raw_dir", "wb_bad_dir", "wb_progress_file",
        "wb_gen_progress_file", "role_output_dir", "minor_role_dir",
        "worldbook_raw_dir", "card_output_dir", "draft_card_dir",
        "worldbook_output_dir", "log_file",
    ]
    for k in path_keys:
        if k in original:
            cfg[k] = original[k]

    # 寫入（保留原始檔案的注釋是做不到的，直接用程式生成乾淨版本）
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print()
    print(green("  ✅ config.yaml 已寫入"))
    print(dim("  注意：原始檔案的注釋已被清除，如需找回請參考備份或 README"))
    print(dim("  若需要修改 global_hint 為多行內容，請直接編輯 config.yaml"))

    # 建立資料目錄
    dirs = [
        "data/chunks", "data/responses/characters", "data/responses/worldbook",
        "data/raw_responses/characters", "data/raw_responses/worldbook",
        "data/bad_chunks/characters", "data/bad_chunks/worldbook",
        "data/roles_json", "data/roles_json_minor", "data/worldbook_raw",
        "data/cards", "data/cards_draft", "data/worldbook", "data/logs",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    print(green(f"  ✅ 資料目錄已建立"))

    pause()


# ============================================================
#  環境檢查
# ============================================================

def run_init():
    """環境檢查：確認套件、小說檔案、config.yaml 是否就緒"""
    print()
    print(bold("  初始化設置"))
    print(dim("  " + "-" * 40))

    # 1. 環境檢查
    print()
    print(cyan("  [1/4] 環境檢查"))
    missing_pkgs = []
    for pkg, import_name in [
        ("openai",   "openai"),
        ("yaml",     "yaml"),
        ("tqdm",     "tqdm"),
    ]:
        try:
            importlib.import_module(import_name)
            print(green(f"    ✅ {pkg}"))
        except ImportError:
            print(red(f"    ❌ {pkg} 未安裝"))
            missing_pkgs.append(pkg)

    if missing_pkgs:
        print()
        print(yellow(f"  請先安裝缺少的套件："))
        print(f"    pip install {' '.join(missing_pkgs)}")
        pause()
        return

    # 2. 小說檔案檢查
    print()
    print(cyan("  [2/4] 小說檔案"))
    novel_path = Path("a.txt")
    if novel_path.exists():
        size = novel_path.stat().st_size
        print(green(f"    ✅ a.txt 存在（{size:,} bytes）"))
    else:
        print(yellow("    ⚠️  找不到 a.txt"))
        print(dim("       請將小說文字檔命名為 a.txt 放在此目錄，"))
        print(dim("       或在 config.yaml 的 input_file 設定其他路徑"))

    # 3. config.yaml 檢查與引導
    print()
    print(cyan("  [3/4] config.yaml 設定"))
    config_path = Path("config.yaml")
    if not config_path.exists():
        print(red("    ❌ config.yaml 不存在"))
        print(dim("       請確認 config.yaml 與 run_all.py 在同一目錄"))
        pause()
        return

    print(green("    ✅ config.yaml 存在"))
    print()
    print(dim("    以下設定需要填入才能正常運作："))

    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    fields = [
        ("api_key",      "性價比模型 API Key（擷取用）"),
        ("api_base",     "性價比模型 API Base URL"),
        ("model",        "性價比模型名稱"),
        ("pro_api_key",  "高性能模型 API Key（生成用）"),
        ("pro_api_base", "高性能模型 API Base URL"),
        ("pro_model",    "高性能模型名稱"),
        ("novel_title",  "小說名稱"),
    ]

    placeholder_values = {"your_api_key_here", "your_pro_api_key_here",
                          "your_model_name_here", "your_pro_model_name_here"}
    all_ok = True
    for key, label in fields:
        val = cfg.get(key, "")
        if not val or str(val) in placeholder_values:
            print(yellow(f"    ⚠️  {key}（{label}）尚未設定"))
            all_ok = False
        else:
            # API key 只顯示前後幾碼
            display = str(val)
            if "key" in key.lower() and len(display) > 8:
                display = display[:4] + "****" + display[-4:]
            print(green(f"    ✅ {key} = {display}"))

    if not all_ok:
        print()
        print(dim("    請用文字編輯器開啟 config.yaml 填入上述設定"))

    # 4. 資料目錄預建
    print()
    print(cyan("  [4/4] 建立資料目錄"))
    dirs = [
        "data/chunks",
        "data/responses/characters",
        "data/responses/worldbook",
        "data/raw_responses/characters",
        "data/raw_responses/worldbook",
        "data/bad_chunks/characters",
        "data/bad_chunks/worldbook",
        "data/roles_json",
        "data/roles_json_minor",
        "data/worldbook_raw",
        "data/cards",
        "data/cards_draft",
        "data/worldbook",
        "data/logs",
    ]
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)
    print(green(f"    ✅ {len(dirs)} 個目錄已確認"))

    print()
    if all_ok:
        print(green("  初始化完成，可以開始使用！"))
    else:
        print(yellow("  請填寫 config.yaml 中標示 ⚠️ 的設定後再開始使用"))

    pause()


# ============================================================
#  主選單邏輯
# ============================================================

def handle_main_menu(choice: str):
    if choice == "a":
        print_flow_preview(FLOW_FULL_CHAR)
        ans = ask("  確認執行？[y/n]：", ["y", "n"])
        if ans == "y":
            run_steps(FLOW_FULL_CHAR)
        pause()

    elif choice == "b":
        print_flow_preview(FLOW_FULL_ALL)
        ans = ask("  確認執行？[y/n]：", ["y", "n"])
        if ans == "y":
            run_steps(FLOW_FULL_ALL)
        pause()

    elif choice == "c":
        print_flow_preview(FLOW_WORLDBOOK)
        ans = ask("  確認執行？[y/n]：", ["y", "n"])
        if ans == "y":
            run_steps(FLOW_WORLDBOOK)
        pause()

    elif choice == "d":
        print()
        print_step_menu()
        valid_keys = [s[0] for s in STEPS]
        chosen = ask_multi(
            f"  請輸入步驟編號（{'/'.join(valid_keys)}），多個以空格分隔：",
            valid_keys,
        )
        if chosen:
            print_flow_preview(chosen)
            ans = ask("  確認執行？[y/n]：", ["y", "n"])
            if ans == "y":
                run_steps(chosen)
        pause()

    elif choice == "s":
        run_setup()

    elif choice == "i":
        run_init()

    elif choice == "q":
        print()
        print(dim("  再見"))
        print()
        sys.exit(0)


def main():
    # 確保 pipeline 模組可以被 import
    sys.path.insert(0, str(Path(__file__).parent))

    valid_keys = [item[0] for item in MAIN_MENU]

    while True:
        print_header()
        print_main_menu()
        choice = ask(f"  請選擇 [{'/'.join(valid_keys)}]：", valid_keys)
        handle_main_menu(choice)


if __name__ == "__main__":
    main()
