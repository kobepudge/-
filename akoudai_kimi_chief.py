# -*- coding: utf-8 -*-
"""
Kimi Chief-Trader 版（AI工程体）入口

说明：
- 仅支持 Chief Trader（合同式 System Prompt + 简报），不再兼容旧的波浪提示模式。
- 运行期仍复用主策略的回调/数据/执行框架，但强制走 Chief Trader 路径。
"""

import json
import time

try:
    import requests  # 直接用 REST 调用，避免依赖 openai SDK
except Exception:
    requests = None

import gkoudai_au_strategy_autonomous as base
from gkoudai_au_strategy_autonomous import *  # noqa: F401,F403 — 复用回调与工具


class KimiConfig:
    BASE_URL = "https://api.moonshot.cn/v1/chat/completions"
    MODEL = "kimi-k2-turbo-preview"
    TEMPERATURE = getattr(base.Config, 'DEEPSEEK_TEMPERATURE', 0.7)
    MAX_TOKENS = getattr(base.Config, 'DEEPSEEK_MAX_TOKENS', 2000)
    API_TIMEOUT = getattr(base.Config, 'API_TIMEOUT', 30)
    API_MAX_RETRIES = getattr(base.Config, 'API_MAX_RETRIES', 3)
    # 建议使用你自己的Key池
    API_KEYS = [
        # TODO: 替换为你的Kimi Key（支持多Key轮询）
        "sk-REPLACE_ME_1",
        "sk-REPLACE_ME_2",
    ]


class AIDecisionEngineKimiChief:
    """Chief Trader 模式的Kimi调用：只使用合同式system，不再保留旧提示。"""

    @staticmethod
    def call_llm(prompt: str, api_key: str = None):  # 新签名
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
                {'role': 'system', 'content': base.SYSTEM_PROMPT_CHIEF_TRADER},
                # prompt 应该是 build_briefing(context, symbol, state) 生成的JSON字符串
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
                    # 提取JSON
                    def _extract_json(txt: str) -> str:
                        t = txt.strip()
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
                        st = t.find('{')
                        if st >= 0:
                            depth = 0
                            for i in range(st, len(t)):
                                ch = t[i]
                                if ch == '{':
                                    depth += 1
                                elif ch == '}':
                                    depth -= 1
                                    if depth == 0:
                                        return t[st:i+1]
                        return t
                    raw = _extract_json(content)
                    def _clean_json(s: str) -> str:
                        import re
                        s2 = re.sub(r",\s*([}\]])", r"\1", s)
                        return s2.replace('None', 'null').replace('True', 'true').replace('False', 'false')
                    try:
                        return json.loads(raw), None
                    except Exception:
                        try:
                            return json.loads(_clean_json(raw)), None
                        except Exception as e:
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
    """先运行主策略初始化，再强制切Chief Trader路径并替换成Kimi引擎。"""
    base.on_init(context)
    try:
        # 强制走 Chief Trader
        try:
            base.Config.USE_CHIEF_TRADER = True
        except Exception:
            pass
        # 替换 Key 池与引擎
        context.key_pool = base.APIKeyPool(KimiConfig.API_KEYS)
        # 适配主策略的调用点：call_deepseek_api(prompt, api_key)
        class _Adapter:
            @staticmethod
            def call_deepseek_api(prompt: str, api_key: str = None):
                return AIDecisionEngineKimiChief.call_llm(prompt, api_key=api_key)
        context.ai_engine = _Adapter()
        Log(f"[AI] Kimi ChiefTrader 就绪: keys={context.key_pool.size()}, model={KimiConfig.MODEL}")
    except Exception as e:
        try:
            Log(f"[AI] Kimi ChiefTrader 初始化异常: {e}")
        except Exception:
            pass


# 其余回调（on_start/on_tick/on_bar/...）复用主策略实现。

