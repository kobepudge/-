# -*- coding: utf-8 -*-
"""
Kimi 版本的自主交易策略入口

做法：复用 gkoudai_au_strategy_autonomous 的全部逻辑，只替换 AI 调用层为 Kimi（Moonshot）API，
并使用多 Key 轮询 + 错峰触发来避免同刻并发。
"""

import json
import time

try:
    import requests  # 与主策略保持一致，直接用 REST 调用，避免依赖 openai SDK
except Exception:
    requests = None

import gkoudai_au_strategy_autonomous as base
from gkoudai_au_strategy_autonomous import *  # noqa: F401,F403 — 导入回调与辅助函数


class KimiConfig:
    BASE_URL = "https://api.moonshot.cn/v1/chat/completions"
    MODEL = "kimi-k2-turbo-preview"
    TEMPERATURE = getattr(base.Config, 'DEEPSEEK_TEMPERATURE', 0.7)
    MAX_TOKENS = getattr(base.Config, 'DEEPSEEK_MAX_TOKENS', 2000)
    API_TIMEOUT = getattr(base.Config, 'API_TIMEOUT', 30)
    API_MAX_RETRIES = getattr(base.Config, 'API_MAX_RETRIES', 3)
    # 多Key（你提供的两把）
    API_KEYS = [
        "sk-Bn613HW6cWJQFdv7wIEmP0GjlNJdgloJUL34AmvYFqBuL6EF",
        "sk-CkIHuWsLYsKE1QOPKuRW0qPmD2BhrYmI2avLDwpau4MT35hY",
    ]


class AIDecisionEngineKimi:
    """与主策略的 AIDecisionEngine 接口保持一致，方法名仍叫 call_deepseek_api。"""

    @staticmethod
    def call_deepseek_api(prompt: str, api_key: str = None):  # 兼容主策略签名
        if requests is None:
            return None, "requests 未安装，跳过Kimi调用"

        key = api_key or (KimiConfig.API_KEYS[0] if KimiConfig.API_KEYS else "")
        if not key:
            return None, "未配置 Kimi API Key"

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {key}',
        }
        payload = {
            'model': KimiConfig.MODEL,
            'messages': [
                {'role': 'system', 'content': base.SYSTEM_PROMPT_WAVE_FIRST},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': KimiConfig.TEMPERATURE,
            'max_tokens': KimiConfig.MAX_TOKENS,
        }

        for attempt in range(int(KimiConfig.API_MAX_RETRIES)):
            try:
                resp = requests.post(
                    KimiConfig.BASE_URL,
                    headers=headers,
                    json=payload,
                    timeout=KimiConfig.API_TIMEOUT,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    content = result['choices'][0]['message']['content']
                    # 容错提取 JSON
                    def _extract_json(txt: str) -> str:
                        t = txt.strip()
                        # 1) 代码块
                        if '```json' in t:
                            try:
                                return t.split('```json', 1)[1].split('```', 1)[0].strip()
                            except Exception:
                                pass
                        if '```' in t:
                            try:
                                return t.split('```', 1)[1].split('```', 1)[0].strip()
                            except Exception:
                                pass
                        # 2) 第一个大括号起、到匹配的右括号
                        start = t.find('{')
                        if start >= 0:
                            # 简单栈匹配
                            depth = 0
                            for i in range(start, len(t)):
                                ch = t[i]
                                if ch == '{':
                                    depth += 1
                                elif ch == '}':
                                    depth -= 1
                                    if depth == 0:
                                        return t[start:i+1]
                        return t
                    raw = _extract_json(content)
                    def _clean_json(s: str) -> str:
                        # 去掉对象/数组中的尾逗号
                        import re
                        s2 = re.sub(r",\s*([}\]])", r"\1", s)
                        # 替换 Python 布尔/空为 JSON
                        s2 = s2.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                        return s2
                    try:
                        return json.loads(raw), None
                    except Exception:
                        try:
                            return json.loads(_clean_json(raw)), None
                        except Exception as e:
                            # 截断错误内容长度，避免日志爆量
                            return None, f"Kimi返回解析失败: {e}"
                else:
                    err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < int(KimiConfig.API_MAX_RETRIES) - 1:
                        time.sleep(2 ** attempt)
                        continue
                    return None, err
            except Exception as e:
                if attempt < int(KimiConfig.API_MAX_RETRIES) - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, f"Kimi调用异常: {e}"

        return None, "Kimi调用失败，已达最大重试次数"


def on_init(context):
    """覆盖 on_init：先执行主策略初始化，再替换 AI 引擎与 Key 池。"""
    base.on_init(context)

    try:
        # 用Kimi的多Key池替换主策略的Key池（沿用主策略的APIKeyPool实现）
        context.key_pool = base.APIKeyPool(KimiConfig.API_KEYS)
        # 替换为Kimi引擎
        context.ai_engine = AIDecisionEngineKimi()
        Log(f"[AI] Kimi引擎就绪: {context.key_pool.size()} 个Key, model={KimiConfig.MODEL}")
    except Exception as e:
        try:
            Log(f"[AI] Kimi初始化异常: {e}")
        except Exception:
            pass


# 其余回调（on_start/on_tick/...）直接复用主策略的实现
