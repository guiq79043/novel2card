# pipeline/04_create_cards.py
# 角色卡生成模組
#
# 讀取 data/roles_json/ 的角色資料，
# 使用高性能模型生成符合 chara_card_v3 規範的 SillyTavern 角色卡。
#
# 成功 → data/cards/{角色名}.json
# 失敗 → data/cards_draft/{角色名}.json（草稿卡，保留原始資料供手動補完）

import time
from pathlib import Path

from core.config import get, load_config
from core.file_utils import (
    read_json, write_json, list_files,
    safe_filename, ensure_dirs,
)
from core.api_client import (
    make_analyze_client, call_api,
    parse_json_response,
)
from core.prompts import get_card_prompt
import core.logger as log


# ============================================================
#  chara_card_v3 結構建構
# ============================================================

def build_chara_card_v3(data: dict, novel_title: str, creator: str) -> dict:
    """
    將模型回傳的 dict 包裝成完整的 chara_card_v3 格式。
    確保所有欄位存在且型別正確。
    """
    base_tags  = [novel_title, "novel2card", "自動生成"]
    model_tags = data.get("tags", [])
    if isinstance(model_tags, list):
        all_tags = base_tags + [t for t in model_tags if t not in base_tags]
    else:
        all_tags = base_tags

    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            # 核心欄位
            "name":        _safe_str(data.get("name",        "未命名角色")),
            "description": _safe_str(data.get("description", "")),
            "personality": _safe_str(data.get("personality", "")),
            "scenario":    _safe_str(data.get("scenario",    "")),

            # SillyTavern 正確欄位名稱是 first_mes
            "first_mes":   _safe_str(data.get("first_mes",   "")),

            # 對話範例（傳統格式）
            "mes_example": _safe_str(data.get("mes_example", "")),

            # 補充欄位
            "creator_notes":             _safe_str(data.get("creator_notes",             "由 novel2card 自動生成")),
            "system_prompt":             _safe_str(data.get("system_prompt",             "")),
            "post_history_instructions": _safe_str(data.get("post_history_instructions", "")),

            # 元資料
            "tags":              all_tags,
            "creator":           creator,
            "character_version": "1.0",

            # 擴充欄位
            "extensions": {},
        },
    }


def _safe_str(val) -> str:
    if val is None:
        return ""
    return str(val)


# ============================================================
#  草稿卡建構（生成失敗時的備援）
# ============================================================

def build_draft_card(character_name: str, entries: list, novel_title: str) -> dict:
    """
    生成失敗時，將原始角色資料包裝成草稿卡。
    格式符合 chara_card_v3，內容欄位保留原始擷取資料，
    讓使用者手動補完後可直接導入 SillyTavern。
    """
    field_labels = {
        "appearance":    "外貌",
        "personality":   "性格",
        "speech_pattern":"說話方式",
        "background":    "背景",
    }

    raw_parts = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        lines = [f"【資料來源 {i}：{entry.get('來源', '未知')}】"]
        for key, label in field_labels.items():
            val = entry.get(key, "")
            if val:
                lines.append(f"{label}：{val}")
        aliases = entry.get("aliases", [])
        if aliases:
            lines.append(f"別名：{'、'.join(aliases)}")
        if len(lines) > 1:
            raw_parts.append("\n".join(lines))

    raw_description = "\n\n".join(raw_parts) if raw_parts else "（無原始資料）"

    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "_draft": True,
        "_draft_reason": "角色卡自動生成失敗，請手動補完各欄位後刪除 _draft 開頭的欄位",
        "data": {
            "name":        character_name,
            "description": f"[草稿 - 請補完]\n\n{raw_description}",
            "personality": "[草稿 - 請補完性格描述]",
            "scenario":    "[草稿 - 請補完情境設定]",
            "first_mes":   "[草稿 - 請補完開場白]",
            "mes_example": "<START>\n{{user}}: [草稿 - 請補完]\n{{char}}: [草稿 - 請補完]",
            "creator_notes":             "此為草稿卡，由 novel2card 在生成失敗時自動建立",
            "system_prompt":             "",
            "post_history_instructions": "",
            "tags":              [novel_title, "novel2card", "草稿", "待補完"],
            "creator":           "novel2card",
            "character_version": "draft",
            "extensions":        {},
        },
    }


# ============================================================
#  角色資料整合（送給模型前的前處理）
# ============================================================

def build_character_summary(entries: list, character_name: str) -> str:
    """將多筆角色擷取記錄整合成結構化文字，送給模型生成角色卡。"""
    field_labels = {
        "appearance":    "外貌特徵",
        "personality":   "性格描述",
        "speech_pattern":"說話方式",
        "background":    "背景資訊",
    }

    parts = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        lines = [f"=== 資料來源 {i}（{entry.get('來源', '未知')}）==="]
        for key, label in field_labels.items():
            val = entry.get(key, "")
            if val:
                lines.append(f"{label}：{val}")
        aliases = entry.get("aliases", [])
        if aliases:
            lines.append(f"別名／稱呼：{'、'.join(aliases)}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    if not parts:
        return f"角色名稱：{character_name}\n（無詳細資料）"

    return f"角色名稱：{character_name}\n\n" + "\n\n".join(parts)


# ============================================================
#  欄位驗證與補完
# ============================================================

_REQUIRED_FIELDS = [
    "name", "description", "personality",
    "scenario", "first_mes", "mes_example",
]


def validate_and_patch(data: dict, character_name: str) -> bool:
    """
    檢查必要欄位，缺少或空白時填入佔位文字（不直接失敗）。
    回傳 True 代表資料基本可用。
    """
    if not isinstance(data, dict):
        log.error(f"{character_name}：模型回傳非 dict 格式")
        return False

    for field in _REQUIRED_FIELDS:
        val = data.get(field)
        if not val or not str(val).strip():
            log.warning(f"{character_name}：欄位 '{field}' 缺失，已填入佔位文字")
            data[field] = f"[待補完：{field}]"

    return True


# ============================================================
#  單一角色卡生成
# ============================================================

def process_single_character(
    character_name: str,
    entries: list,
    client,
    pro_model: str,
    novel_title: str,
    creator: str,
    thinking_mode,
) -> dict | None:
    """
    對單一角色執行角色卡生成。
    成功回傳 chara_card_v3 dict，失敗回傳 None。
    """
    summary     = build_character_summary(entries, character_name)
    card_prompt = get_card_prompt()

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位專業的 SillyTavern 角色卡製作 AI，"
                "擅長將小說角色資料轉化為生動且一致的角色卡。"
                "請只回傳 JSON 格式的結果，不要包含任何額外說明或 markdown 標記。"
            ),
        },
        {
            "role": "user",
            "content": card_prompt + summary,
        },
    ]

    raw_content = call_api(
        client=client,
        model=pro_model,
        messages=messages,
        temperature=0.5,
        max_tokens=4096,
        timeout=300,
        thinking_mode=thinking_mode,
    )

    if raw_content is None:
        log.error(f"{character_name}：API 呼叫失敗")
        return None

    parsed = parse_json_response(raw_content, client, pro_model)
    if parsed is None:
        log.error(f"{character_name}：JSON 解析徹底失敗")
        return None

    if not validate_and_patch(parsed, character_name):
        return None

    return build_chara_card_v3(parsed, novel_title, creator)


# ============================================================
#  主流程
# ============================================================

def main():
    load_config()

    # 路徑
    role_dir  = Path(get("role_output_dir",  "data/roles_json"))
    card_dir  = Path(get("card_output_dir",  "data/cards"))
    draft_dir = Path(get("draft_card_dir",   "data/cards_draft"))
    ensure_dirs(card_dir, draft_dir)

    # 設定
    pro_model       = get("pro_model")
    novel_title     = get("novel_title",   "未命名小說")
    creator         = get("card_creator",  "novel2card")
    force_overwrite = get("force_overwrite", False)
    thinking_mode   = get("analyze_thinking_mode", "auto")
    if thinking_mode in ("false", False):
        thinking_mode = False

    # 驗證必要設定
    if not get("pro_api_key"):
        log.error("找不到 pro_api_key，請在 config.yaml 中設定")
        return
    if not pro_model:
        log.error("找不到 pro_model，請在 config.yaml 中設定")
        return

    client = make_analyze_client()

    # 排除統計檔
    role_files = [
        f for f in list_files(role_dir, "*.json")
        if f.name != "character_stats.json"
    ]

    if not role_files:
        log.error(f"在 {role_dir} 中找不到角色 JSON 檔案，請先執行步驟 03")
        return

    log.section(f"角色卡生成開始｜共 {len(role_files)} 個角色")

    success_count = 0
    skip_count    = 0
    draft_count   = 0
    error_count   = 0

    for role_file in log.progress(role_files, desc="生成角色卡", unit="個"):
        character_name = role_file.stem
        safe_name      = safe_filename(character_name)
        card_path      = card_dir  / f"{safe_name}.json"
        draft_path     = draft_dir / f"{safe_name}.json"

        # 跳過已存在
        if card_path.exists() and not force_overwrite:
            log.debug(f"跳過 {character_name}（已存在）")
            skip_count += 1
            continue

        # 讀取角色資料
        try:
            entries = read_json(role_file)
        except Exception as e:
            log.error(f"{character_name}：讀取失敗 → {e}")
            error_count += 1
            continue

        if not isinstance(entries, list) or len(entries) == 0:
            log.warning(f"{character_name}：資料格式錯誤或為空，跳過")
            error_count += 1
            continue

        log.info(f"處理：{character_name}（{len(entries)} 筆來源資料）")

        # 生成角色卡
        card = process_single_character(
            character_name=character_name,
            entries=entries,
            client=client,
            pro_model=pro_model,
            novel_title=novel_title,
            creator=creator,
            thinking_mode=thinking_mode,
        )

        if card is not None:
            write_json(card_path, card)
            log.success(f"✓ {card['data']['name']} → {card_path.name}")
            success_count += 1
        else:
            draft = build_draft_card(character_name, entries, novel_title)
            write_json(draft_path, draft)
            log.warning(f"✗ {character_name} → 草稿卡已儲存至 {draft_path.name}")
            draft_count += 1

        time.sleep(2)

    # 統計
    log.section("角色卡生成完成")
    log.success(f"成功：{success_count} 張")
    if skip_count:
        log.info(f"跳過：{skip_count} 個（已存在）")
    if draft_count:
        log.warning(
            f"草稿：{draft_count} 張\n"
            f"  → 請至 {draft_dir}/ 手動補完後即可導入 SillyTavern"
        )
    if error_count:
        log.error(f"錯誤：{error_count} 個（請查看日誌）")


if __name__ == "__main__":
    main()
