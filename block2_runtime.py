"""Deterministic Block 2 intent and Chinese script resolution helpers."""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from language_blocks import LANGUAGE_MODE_BLOCK2


# Only script-specific characters participate. Shared Han characters are neutral.
# The list favors common game UI, dialogue, quest, inventory and system vocabulary.
_SCRIPT_SPECIFIC_PAIRS = (
    ("这", "這"), ("为", "為"), ("国", "國"), ("发", "發"), ("后", "後"),
    ("里", "裡"), ("时", "時"), ("会", "會"), ("来", "來"), ("对", "對"),
    ("说", "說"), ("门", "門"), ("见", "見"), ("车", "車"), ("书", "書"),
    ("风", "風"), ("云", "雲"), ("龙", "龍"), ("马", "馬"), ("鸟", "鳥"),
    ("鱼", "魚"), ("画", "畫"), ("话", "話"), ("语", "語"), ("东", "東"),
    ("叶", "葉"), ("万", "萬"), ("与", "與"), ("业", "業"), ("开", "開"),
    ("关", "關"), ("电", "電"), ("脑", "腦"), ("网", "網"), ("线", "線"),
    ("级", "級"), ("战", "戰"), ("术", "術"), ("医", "醫"), ("药", "藥"),
    ("气", "氣"), ("体", "體"), ("进", "進"), ("远", "遠"), ("队", "隊"),
    ("长", "長"), ("岛", "島"), ("台", "臺"), ("广", "廣"), ("厂", "廠"),
    ("厅", "廳"), ("历", "歷"), ("压", "壓"), ("复", "複"), ("杂", "雜"),
    ("继", "繼"), ("续", "續"), ("设", "設"), ("选", "選"), ("项", "項"),
    ("读", "讀"), ("档", "檔"), ("确", "確"), ("认", "認"), ("装", "裝"),
    ("备", "備"), ("务", "務"), ("图", "圖"), ("斗", "鬥"), ("击", "擊"),
    ("经", "經"), ("验", "驗"), ("获", "獲"), ("奖", "獎"), ("励", "勵"),
    ("购", "購"), ("买", "買"), ("数", "數"), ("当", "當"), ("状", "狀"),
    ("态", "態"), ("余", "餘"), ("载", "載"), ("删", "刪"), ("频", "頻"),
    ("视", "視"), ("难", "難"), ("简", "簡"), ("单", "單"), ("络", "絡"),
    ("联", "聯"), ("机", "機"), ("邮", "郵"), ("鉴", "鑑"), ("帮", "幫"),
    ("锁", "鎖"), ("闭", "閉"), ("动", "動"), ("职", "職"), ("实", "實"),
    ("处", "處"), ("传", "傳"), ("给", "給"), ("坏", "壞"), ("旧", "舊"),
    ("灭", "滅"), ("过", "過"), ("边", "邊"), ("声", "聲"), ("无", "無"),
    ("敌", "敵"), ("胜", "勝"), ("败", "敗"), ("伤", "傷"), ("弹", "彈"),
    ("护", "護"), ("隐", "隱"), ("显", "顯"), ("触", "觸"), ("钱", "錢"),
    ("师", "師"), ("纪", "紀"), ("卫", "衛"), ("寻", "尋"), ("导", "導"),
    ("标", "標"), ("题", "題"), ("质", "質"), ("优", "優"), ("势", "勢"),
    ("键", "鍵"), ("盘", "盤"), ("层", "層"), ("栏", "欄"), ("览", "覽"),
    ("编", "編"), ("译", "譯"), ("统", "統"), ("总", "總"), ("页", "頁"),
    ("条", "條"), ("头", "頭"), ("块", "塊"), ("币", "幣"), ("产", "產"),
)
# These left-side forms are also normal Traditional/common Han in another meaning.
# They must not be treated as proof of Simplified script on their own:
# 后 (empress), 里 (distance/neighborhood), 台 (Taiwan/platform), 斗 (dipper),
# 余 (surname/pronoun), 云 (to say/classical usage).
_AMBIGUOUS_SIMPLIFIED_FORMS = frozenset({"后", "里", "台", "斗", "余", "云"})
_STRONG_SIMPLIFIED_EVIDENCE = frozenset(
    pair[0] for pair in _SCRIPT_SPECIFIC_PAIRS if pair[0] not in _AMBIGUOUS_SIMPLIFIED_FORMS
)
_STRONG_TRADITIONAL_EVIDENCE = frozenset(pair[1] for pair in _SCRIPT_SPECIFIC_PAIRS)


class ChineseScriptResolver:
    def __init__(self) -> None:
        self._last_confident: dict[str, str] = {}
        self._lock = threading.Lock()

    def resolve_detailed(
        self,
        text: str,
        mode: str = "auto",
        session_key: str = "default",
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "auto").strip().lower().replace("-", "_")
        if normalized_mode in {"simplified", "zh_hans"}:
            normalized_mode = "zh"
        elif normalized_mode in {"traditional", "zh_traditional"}:
            normalized_mode = "zh_hant"
        elif normalized_mode not in {"auto", "zh", "zh_hant"}:
            normalized_mode = "auto"

        source_text = str(text or "")
        simplified = sum(char in _STRONG_SIMPLIFIED_EVIDENCE for char in source_text)
        traditional = sum(char in _STRONG_TRADITIONAL_EVIDENCE for char in source_text)
        ambiguous = sum(char in _AMBIGUOUS_SIMPLIFIED_FORMS for char in source_text)
        key = str(session_key or "default")
        with self._lock:
            previous = str(self._last_confident.get(key) or "")
            decision = "zh"
            reason = "auto_neutral_default"
            if normalized_mode in {"zh", "zh_hant"}:
                decision = normalized_mode
                reason = "explicit"
            elif simplified > traditional:
                self._last_confident[key] = "zh"
                decision = "zh"
                reason = "auto_simplified_confident"
            elif traditional > simplified:
                self._last_confident[key] = "zh_hant"
                decision = "zh_hant"
                reason = "auto_traditional_confident"
            elif previous:
                decision = previous
                reason = "auto_neutral_last_confident"
            new_state = str(self._last_confident.get(key) or "")
        return {
            "configured_chinese_mode": normalized_mode,
            "previous_auto_state": previous,
            "simplified_evidence": int(simplified),
            "traditional_evidence": int(traditional),
            "ambiguous_evidence": int(ambiguous),
            "decision": decision,
            "decision_reason": reason,
            "effective_source_lang": decision,
            "new_auto_state": new_state,
            "source_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:12],
        }

    def resolve(self, text: str, mode: str = "auto", session_key: str = "default") -> tuple[str, str]:
        result = self.resolve_detailed(text, mode=mode, session_key=session_key)
        return str(result["effective_source_lang"]), str(result["decision_reason"])

    def reset(self, session_key: str | None = None) -> None:
        with self._lock:
            if session_key is None:
                self._last_confident.clear()
            else:
                self._last_confident.pop(str(session_key), None)


def is_block2_session(session_data: dict[str, Any] | None) -> bool:
    session = session_data or {}
    translation = session.get("translation") if isinstance(session.get("translation"), dict) else {}
    return str(session.get("language_mode") or translation.get("language_mode") or "") == LANGUAGE_MODE_BLOCK2


def block2_request_context(
    session_data: dict[str, Any] | None,
    *,
    source_text: str = "",
    runtime_mode: str = "overlay",
) -> dict[str, Any]:
    session = session_data or {}
    translation = session.get("translation") if isinstance(session.get("translation"), dict) else {}
    if not is_block2_session(session):
        return {}
    source = str(translation.get("source_lang") or "ja").strip().lower()
    target = str(translation.get("target_lang") or "en").strip().lower()
    profile = str(translation.get("translation_profile") or "fast").strip().lower()
    script_mode = str(translation.get("chinese_script_mode") or "auto").strip().lower().replace("-", "_")
    overrides = translation.get("advanced_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    return {
        "block": 2,
        "profile_id": str(session.get("profile_id") or session.get("profile_path") or "session"),
        "source_language": source if source in {"ja", "zh"} else "ja",
        "target_language": target if target in {"en", "ru", "uk"} else "en",
        "translation_profile": profile if profile in {"fast", "quality"} else "fast",
        "chinese_script_mode": script_mode if script_mode in {"auto", "zh", "zh_hant"} else "auto",
        "runtime_mode": "dynamic" if str(runtime_mode).startswith("dynamic") else "overlay",
        "advanced_overrides": dict(overrides),
        "source_text": str(source_text or ""),
    }


__all__ = ["ChineseScriptResolver", "block2_request_context", "is_block2_session"]
