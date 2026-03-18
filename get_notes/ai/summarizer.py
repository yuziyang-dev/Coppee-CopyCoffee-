"""
手冲咖啡方案提取器：两步流程 —— 精准提取 + AI 智能补全。

Step 1: 从内容中提取明确提到的参数（缺失留空）
Step 2: 根据已有参数推理补全缺失字段，标记哪些是补全的
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

from get_notes.config import AppConfig
from get_notes.models import BrewCard, ParsedContent

logger = logging.getLogger(__name__)

# ── Step 1: 精准提取 Prompt ──

EXTRACT_PROMPT = """\
你是一个专业的手冲咖啡方案分析师。用户从小红书或抖音上复制了一个关于手冲咖啡的视频或图文内容。

你的任务是从内容中**精准提取**冲煮参数。

## 严格规则

1. **只提取内容中明确提到的参数**
2. **没提到的字段必须留空字符串 ""**，绝对不要猜测或编造
3. 数值保留原文表述（如 "C40 24格" 不要转换）
4. 如果作者没直接说粉水比，但给了粉量和水量，可以计算出来（这算提取不算猜测）
5. 注水步骤按时间顺序拆分每一段
6. 口语化转录需书面化处理

## 方案命名规则（title 字段）

请判断这个方案的适用范围，尽量写出**通用化**的方案名称：

1. **识别通用性**：大多数方案其实是针对某一类豆子的通用冲法，而不是只适用于某一款具体豆子。比如作者用"花魁"演示，但方案本质上适用于所有"日晒浅烘非洲豆"
2. **命名优先级**：
   - 优先用「烘焙度 + 产区/风味类型 + 滤杯 + 风格关键词」来命名
   - 好的例子：「浅烘非洲豆 V60 高甜感方案」「中深烘 Kalita 醇厚方案」「浅烘花果调 一刀流方案」
   - 避免的例子：「xx品牌 花魁6.0 冲煮方案」（太具体，换个豆子用户就觉得不适用了）
3. **例外**：如果作者明确强调此方案只针对某款特定豆子，才用具体豆名
4. **summary** 中可以补充说明适用的豆子类型范围

## 输出 JSON（缺失字段留空 ""）

{
  "title": "通用化方案名称（见上方命名规则）",
  "summary": "一句话概括方案特点和适用豆子类型",
  "bean_name": "", "origin": "", "roast_level": "", "process": "", "bean_notes": "",
  "grind_size": "",
  "dose": "", "water_amount": "", "ratio": "", "water_temp": "", "total_time": "",
  "dripper": "", "filter_paper": "", "equipment_notes": "",
  "pour_steps": [
    {
      "stage": "阶段名，如：闷蒸 / 第一段 / 第二段",
      "water_ml": "本段注水量或累计水量，如 30ml / 注至150ml",
      "time": "时间点或时间段，如 0:00-0:30 / 30s",
      "technique": "注水手法，如 中心注水、绕圈、搅拌"
    }
  ],
  "flavor_notes": "", "tips": ""
}

注意：pour_steps 数组中每个对象必须使用上面的英文 key（stage, water_ml, time, technique），值用中文填写。按时间顺序排列。
"""

# ── Step 2: 智能补全 Prompt ──

INFER_PROMPT = """\
你是一个资深手冲咖啡师。下面是从一个手冲方案中提取到的参数，部分字段为空（未在原内容中提及）。

请根据已有参数和你的专业知识，**推理补全**那些为空的字段。

## 补全规则

1. **已有值的字段绝对不能修改**，原封不动保留
2. 只补全值为空字符串 "" 的字段
3. 补全要合理：根据已知参数推理，而不是随意填写
4. 推理逻辑示例：
   - 有粉量和水量 → 算出粉水比
   - 有产地是耶加雪菲 → 烘焙度大概率是浅烘，处理法常见水洗或日晒
   - 有 V60 滤杯 + 浅烘 → 水温大约 90-93°C，研磨度中细
   - 有注水步骤的时间信息 → 推算总萃取时间
   - 有浅烘非洲豆 → 风味可能是花香、柑橘、莓果类
   - 有粉量 15g → 常见水量 225-240ml
5. 如果实在无法合理推断，保持空字符串
6. **pour_steps 补全规则**：
   - 不要增删步骤，保留原有步骤数量和顺序
   - 每个步骤中，已有值的字段不动
   - 但如果某个步骤的 time 为空，请根据上下文推理时间段（结合前一步骤时间、总水量、注水速率等），格式如 "0:30-1:00"
   - 推理逻辑：闷蒸一般 0:00-0:30，后续步骤根据水量和常见流速推算，确保最后一步的结束时间与 total_time 接近

## 输出格式

返回一个 JSON，包含两个字段：
{
  "card": { ... 完整的方案卡 JSON，已有字段不变，空字段填入推理值 ... },
  "inferred": ["field1", "field2", ...]
}

其中 inferred 数组列出你补全了哪些字段名（英文字段名）。
只列出你实际填入了新值的字段，没有补全的不要列。
如果补全了 pour_steps 中步骤的时间，也要在 inferred 中加入 "pour_steps"。
"""


INFERABLE_FIELDS = [
    "bean_name", "origin", "roast_level", "process", "bean_notes",
    "grind_size", "dose", "water_amount", "ratio", "water_temp",
    "total_time", "dripper", "filter_paper", "flavor_notes",
]


class NoteSummarizer:
    """手冲咖啡方案提取器（两步：提取 + 补全）"""

    def __init__(self, config: AppConfig):
        self.config = config

    def summarize(
        self,
        aggregated_text: str,
        parsed: ParsedContent,
        user_instruction: Optional[str] = None,
    ) -> BrewCard:
        if not self.config.llm.api_key or "填入" in self.config.llm.api_key:
            logger.warning("LLM API未配置，生成基础卡片")
            return self._basic_card(aggregated_text, parsed)

        user_message = f"请从以下内容中提取手冲咖啡方案参数：\n\n{aggregated_text}"
        if user_instruction:
            user_message += f"\n\n用户补充要求：{user_instruction}"

        try:
            # Step 1: 精准提取
            logger.info("Step 4a: AI精准提取方案参数")
            raw = self._call_llm(EXTRACT_PROMPT, user_message)
            card = self._parse_card(raw, parsed)
            card.raw_content = aggregated_text
            card.author = parsed.author

            # Step 2: 检查是否有空字段或步骤缺时间，需要补全
            missing = [f for f in INFERABLE_FIELDS if not getattr(card, f, "")]
            steps_missing_time = any(
                not s.get("time", "") for s in card.pour_steps
            ) if card.pour_steps else False
            if missing or steps_missing_time:
                reasons = []
                if missing:
                    reasons.append(f"{len(missing)}个空字段")
                if steps_missing_time:
                    reasons.append("注水步骤缺时间")
                logger.info("Step 4b: AI补全缺失 (%s)", ", ".join(reasons))
                card = self._infer_missing(card)
            else:
                logger.info("所有字段已提取，无需补全")

            return card
        except Exception as e:
            logger.error("AI提取失败: %s", e)
            return self._basic_card(aggregated_text, parsed)

    def _infer_missing(self, card: BrewCard) -> BrewCard:
        """第二步：将已提取的卡片发给 LLM 推理补全空字段。"""
        card_dict = {
            "title": card.title, "summary": card.summary,
            "bean_name": card.bean_name, "origin": card.origin,
            "roast_level": card.roast_level, "process": card.process,
            "bean_notes": card.bean_notes, "grind_size": card.grind_size,
            "dose": card.dose, "water_amount": card.water_amount,
            "ratio": card.ratio, "water_temp": card.water_temp,
            "total_time": card.total_time,
            "dripper": card.dripper, "filter_paper": card.filter_paper,
            "equipment_notes": card.equipment_notes,
            "pour_steps": card.pour_steps,
            "flavor_notes": card.flavor_notes, "tips": card.tips,
        }

        user_message = (
            f"以下是从内容中提取到的手冲方案参数（空字符串表示未提及）：\n\n"
            f"{json.dumps(card_dict, ensure_ascii=False, indent=2)}\n\n"
            f"请根据已有参数推理补全空字段。"
        )

        try:
            raw = self._call_llm(INFER_PROMPT, user_message)
            data = self._parse_json(raw)

            inferred_card = data.get("card", {})
            inferred_fields = data.get("inferred", [])

            # 只更新原本为空的字段，已有值绝不覆盖
            for field_name in inferred_fields:
                if field_name == "pour_steps":
                    continue
                if field_name not in INFERABLE_FIELDS:
                    continue
                original_val = getattr(card, field_name, "")
                if original_val:
                    continue
                new_val = inferred_card.get(field_name, "")
                if new_val:
                    setattr(card, field_name, new_val)

            # pour_steps: 只补全步骤内部的空字段，不增删步骤
            if "pour_steps" in inferred_fields:
                new_steps = inferred_card.get("pour_steps", [])
                if new_steps and len(new_steps) == len(card.pour_steps):
                    for i, orig_step in enumerate(card.pour_steps):
                        for key in ("stage", "water_ml", "time", "technique"):
                            if not orig_step.get(key, "") and new_steps[i].get(key, ""):
                                orig_step[key] = new_steps[i][key]
                                orig_step.setdefault("_inferred", [])
                                orig_step["_inferred"].append(key)

            actual_inferred = [
                f for f in inferred_fields
                if (f in INFERABLE_FIELDS and inferred_card.get(f, ""))
                or f == "pour_steps"
            ]
            card.inferred_fields = actual_inferred

            logger.info("AI补全了 %d 个字段: %s", len(card.inferred_fields), card.inferred_fields)

        except Exception as e:
            logger.warning("AI补全失败（不影响已提取内容）: %s", e)

        return card

    def _call_llm(self, system_prompt: str, user_message: str) -> str:
        llm = self.config.llm

        max_input_chars = 60000
        if len(user_message) > max_input_chars:
            user_message = user_message[:max_input_chars] + "\n\n[内容过长，已截断]"

        resp = requests.post(
            f"{llm.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {llm.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": llm.model,
                "temperature": 0.2,
                "max_tokens": llm.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        json_str = raw.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', json_str, re.DOTALL)
            return json.loads(match.group()) if match else {}

    def _parse_card(self, raw: str, parsed: ParsedContent) -> BrewCard:
        data = self._parse_json(raw)
        return BrewCard(
            title=data.get("title", parsed.title or ""),
            summary=data.get("summary", ""),
            bean_name=data.get("bean_name", ""),
            origin=data.get("origin", ""),
            roast_level=data.get("roast_level", ""),
            process=data.get("process", ""),
            bean_notes=data.get("bean_notes", ""),
            grind_size=data.get("grind_size", ""),
            dose=data.get("dose", ""),
            water_amount=data.get("water_amount", ""),
            ratio=data.get("ratio", ""),
            water_temp=data.get("water_temp", ""),
            total_time=data.get("total_time", ""),
            dripper=data.get("dripper", ""),
            filter_paper=data.get("filter_paper", ""),
            equipment_notes=data.get("equipment_notes", ""),
            pour_steps=data.get("pour_steps", []),
            flavor_notes=data.get("flavor_notes", ""),
            tips=data.get("tips", ""),
            source_platform=parsed.platform.value,
            source_url=parsed.source_url,
        )

    @staticmethod
    def _basic_card(text: str, parsed: ParsedContent) -> BrewCard:
        summary = text[:300].strip()
        if len(text) > 300:
            summary += "..."
        return BrewCard(
            title=parsed.title or "手冲方案",
            summary=summary,
            author=parsed.author,
            source_platform=parsed.platform.value,
            source_url=parsed.source_url,
            raw_content=text,
        )
