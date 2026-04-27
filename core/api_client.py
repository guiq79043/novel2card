# core/api_client.py
# 統一 API 客戶端
# 處理：think 自動偵測、重試退避、JSON 搶救

import re
import json
import time
from openai import OpenAI

import core.logger as log
from core.config import get


# ============================================================
#  Think 內容清理
# ============================================================

def _strip_think_tags(text: str) -> str:
    """移除 <think>...</think> 標籤及其內容（DeepSeek / Qwen 等）"""
    if not text:
        return ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _extract_content(response, model_has_thinking: bool | str) -> str:
    """
    從 API 回應中安全地取出純文字內容。

    think 模式判斷：
    - config 設定 thinking_mode: "auto"（預設）→ 自動偵測
    - config 設定 thinking_mode: false          → 完全不處理 think
    - 此函數的 model_has_thinking 參數由呼叫端依 config 傳入

    自動偵測邏輯：
    1. 嘗試讀取 reasoning_content 欄位（DeepSeek R1 via OpenAI 相容 API）
    2. 嘗試移除 <think>...</think> 標籤
    3. 若以上都沒有，直接回傳 content
    """
    message = response.choices[0].message

    # thinking_mode: false → 直接回傳 content，不做任何處理
    if model_has_thinking is False:
        content = getattr(message, "content", None) or ""
        return content.strip()

    # 取得主要 content
    content = getattr(message, "content", None) or ""

    # 自動偵測：檢查是否有獨立的 reasoning_content 欄位
    # （DeepSeek R1 透過 OpenAI 相容端點時的格式）
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        log.debug(f"偵測到 reasoning_content 欄位（{len(reasoning)} 字），已略過")
        # content 已經是純回答，不需要額外處理
        return content.strip()

    # 自動偵測：嘗試移除 <think> 標籤
    if "<think>" in content.lower():
        log.debug("偵測到 <think> 標籤，正在移除")
        content = _strip_think_tags(content)

    return content.strip()


# ============================================================
#  JSON 搶救
# ============================================================

def _rescue_json_regex(raw: str) -> any:
    """
    第一層搶救：用 regex 從原始文字中提取 JSON。
    處理模型在 JSON 前後多輸出說明文字的情況。
    """
    # 嘗試提取第一個完整的 JSON 陣列
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group())
        except json.JSONDecodeError:
            pass

    # 嘗試提取第一個完整的 JSON 物件
    obj_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    return None


def _rescue_json_model(raw: str, client: OpenAI, model: str) -> any:
    """
    第二層搶救：讓高性能模型重新格式化損壞的 JSON。
    使用高性能模型（pro_model）以提高成功率。
    """
    log.warning("JSON regex 搶救失敗，嘗試呼叫模型修復...")

    repair_messages = [
        {
            "role": "system",
            "content": (
                "你是一個 JSON 修復工具。\n"
                "使用者會提供一段損壞或格式不正確的 JSON 文字，"
                "你必須回傳修復後的合法 JSON，不得包含任何額外文字、說明或 markdown 標記。\n"
                "只回傳純 JSON。"
            ),
        },
        {
            "role": "user",
            "content": f"請修復以下 JSON：\n\n{raw}",
        },
    ]

    try:
        repair_response = client.chat.completions.create(
            model=model,
            messages=repair_messages,
            temperature=0.0,
            max_tokens=8192,
            timeout=120,
        )
        repaired_text = getattr(repair_response.choices[0].message, "content", "") or ""
        repaired_text = _strip_think_tags(repaired_text).strip()

        # 移除可能的 markdown 包裝
        repaired_text = re.sub(r"^```(?:json)?\s*", "", repaired_text)
        repaired_text = re.sub(r"\s*```$", "", repaired_text)

        return json.loads(repaired_text)

    except Exception as e:
        log.error(f"模型 JSON 修復失敗：{e}")
        return None


def parse_json_response(raw: str, client: OpenAI, pro_model: str) -> any:
    """
    嘗試解析 JSON，失敗時依序嘗試：
    1. 直接解析
    2. regex 搶救
    3. 模型修復（使用高性能模型）
    回傳解析結果，全部失敗時回傳 None。
    """
    if not raw:
        return None

    # 移除可能的 markdown code block 包裝
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    # 第一層：直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    log.warning("直接解析失敗，嘗試 regex 搶救...")

    # 第二層：regex 搶救
    rescued = _rescue_json_regex(cleaned)
    if rescued is not None:
        log.success("regex 搶救成功")
        return rescued

    # 第三層：模型修復
    rescued = _rescue_json_model(cleaned, client, pro_model)
    if rescued is not None:
        log.success("模型修復成功")
        return rescued

    log.error("所有 JSON 搶救方式均失敗")
    return None


# ============================================================
#  API 客戶端工廠
# ============================================================

def make_client(api_key: str, api_base: str) -> OpenAI:
    """建立 OpenAI 相容客戶端"""
    return OpenAI(api_key=api_key, base_url=api_base)


def make_extract_client() -> OpenAI:
    """建立資料擷取用客戶端（性價比模型）"""
    return make_client(
        api_key=get("api_key"),
        api_base=get("api_base", "https://api.openai.com/v1"),
    )


def make_analyze_client() -> OpenAI:
    """建立資料分析用客戶端（高性能模型）"""
    return make_client(
        api_key=get("pro_api_key"),
        api_base=get("pro_api_base", "https://api.openai.com/v1"),
    )


# ============================================================
#  統一 API 呼叫（含重試、退避、think 處理）
# ============================================================

def call_api(
    client: OpenAI,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.4,
    max_tokens: int = 8192,
    timeout: int = 300,
    thinking_mode: str | bool = "auto",  # "auto" | False
    extra_params: dict = None,           # 傳給模型的額外參數（如 thinking budget）
) -> str | None:
    """
    統一 API 呼叫入口。

    - 自動處理重試（指數退避）
    - 自動處理 think 內容過濾
    - 區分錯誤類型：限流 / 格式 / 網路

    回傳純文字內容，失敗時回傳 None。
    """
    retry_limit = get("retry_limit", 3)
    params = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    if extra_params:
        params.update(extra_params)

    last_error = None
    for attempt in range(1, retry_limit + 1):
        try:
            response = client.chat.completions.create(**params)
            content = _extract_content(response, thinking_mode)

            if content is None:
                raise ValueError("API 回傳空內容")

            return content

        except Exception as e:
            last_error = e
            err_str = str(e)

            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower()
            is_last = attempt == retry_limit

            if is_last:
                log.error(f"API 呼叫失敗（第 {attempt} 次，已達上限）：{err_str}")
                return None

            wait = (4 ** attempt) if is_rate_limit else (2 * attempt)
            log.warning(
                f"API 呼叫失敗（第 {attempt}/{retry_limit} 次）：{err_str}\n"
                f"  → {'限流，' if is_rate_limit else ''}等待 {wait} 秒後重試"
            )
            time.sleep(wait)

    return None
