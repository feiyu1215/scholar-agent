"""
core/tool_schemas.py — 工具 Schema 中心注册表

所有工具的 JSON Schema 定义集中管理。
各 Persona 通过名称列表引用，结合可选的 description 覆盖来定制工具行为。

设计原则:
    1. 每个工具的 input_schema 只定义一次（Single Source of Truth）
    2. description 可以按 persona 定制（LLM 看到的描述影响行为）
    3. identity.py 仅负责认知身份文本和 prompt 组装，不再承载 schema 定义
    4. 新增工具只需在本文件添加一处定义 + 在 PERSONA_TOOL_NAMES 中注册
"""

from __future__ import annotations

from typing import Any

from core.plugin_installer import MANAGE_PLUGINS_SCHEMA as _MANAGE_PLUGINS_SCHEMA


# ============================================================
# 工具 Schema 注册表 — 每个工具定义一次
# ============================================================

TOOL_REGISTRY: dict[str, dict[str, Any]] = {}


def _reg(schema: dict[str, Any]) -> None:
    """注册一个工具 schema。"""
    name = schema["name"]
    if name in TOOL_REGISTRY:
        raise ValueError(f"Duplicate tool schema registration: {name}")
    TOOL_REGISTRY[name] = schema


# ------------------------------------------------------------------
# 以下逐个注册所有工具（按功能分组）
# ------------------------------------------------------------------

# --- 核心阅读 ---

_reg({
    "name": "read_section",
    "description": (
        "读取论文的某个部分。你可以指定 section 名称（如 'introduction', "
        "'methodology', 'results'），或者 'full' 读全文。对于长 section，"
        "每次返回最多 6000 字符；如果被截断，返回信息中会告诉你如何用 offset 续读剩余部分。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "要读取的 section 名称，或 'full' 读全文，或 'list' 列出所有 sections"
            },
            "offset": {
                "type": "integer",
                "description": "从第几个字符开始读取（用于续读被截断的长 section）。首次读取不需要设置此参数。"
            }
        },
        "required": ["section"]
    }
})

# --- 文献搜索 ---

_reg({
    "name": "search_literature",
    "description": (
        "你的 Google Scholar——搜索学术文献来校准你的判断。核心原则：你的知识有边界。"
        "你大概知道很多方法和理论，但对具体的数值范围、最新进展、已知局限的细节，"
        "你的记忆可能过时或模糊。当你意识到自己'大概知道但说不清细节'时，这就是该搜索的时刻。"
        "\n\nWHEN TO USE（知识边界信号——当你遇到以下情况时，搜索而非猜测）：\n"
        "(1) 具体数值判断：论文报告了效应量、弹性系数、标准误、bandwidth 等数值，"
        "你需要判断其合理性——搜索同类研究的典型范围。"
        "你可能知道'DID 的效应量一般不大'，但你不确定这个领域 0.3 SD 算大还是小。\n"
        "(2) 方法论的已知局限：你遇到一个估计方法（如 synthetic control、bunching estimator、shift-share IV），"
        "你知道它的基本原理，但不确定它在最新文献中被指出了哪些具体问题——搜索其局限性和最佳实践。\n"
        "(3) 参数选择的合理性：论文选择了某个 bandwidth、cluster level、bootstrap iterations 数值，"
        "你不确定这是否符合最佳实践——搜索该方法的实施指南。\n"
        "(4) Novelty 验证：论文声称'first to'或'no prior work'——搜索确认是否真的没有先例。\n"
        "(5) 引用核查：关键引用的作者/年份/结论是否被正确引用——搜索确认。\n"
        "(6) 核心 claim 的外部验证：你对论文的结论有了判断，想看其他研究是否支持或反驳——搜索交叉验证。\n"
        "(7) 方法是否被 supersede：论文使用的方法可能已有更优替代——搜索确认该方法在当前文献中的地位。\n"
        "\nWHEN NOT TO USE（不需要搜索的情况）：\n"
        "- 你对一个纯逻辑问题有确定判断（如'这个证明第三步有跳跃'）——这不需要外部验证\n"
        "- 你在描述论文做了什么（理解层）——搜索是为了质疑和验证，不是为了理解\n"
        "- 你已经搜过同一个问题且结果清晰——不要重复搜索\n"
        "\n如果你审完一篇论文却从未搜索过文献，问自己：你对方法论细节的判断是基于确切知识，"
        "还是基于'我大概记得是这样'？后者就是该搜索的信号。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询词（英文效果最好，用关键术语+作者名或方法名）"
            },
            "reason": {
                "type": "string",
                "description": "你为什么要搜索这个——帮助你自己保持意图清晰，避免漫无目的的搜索"
            }
        },
        "required": ["query", "reason"]
    }
})

_reg({
    "name": "fetch_paper_detail",
    "description": (
        "深入了解一篇外部论文——就像你从书架上拿下一篇论文翻开来看。"
        "search_literature 给你的是搜索结果页（标题+摘要片段），"
        "而 fetch_paper_detail 让你看到完整摘要、TLDR、该论文引用了谁、谁引用了它。"
        "典型使用场景：(1) 搜索结果中某篇论文的方法论看起来和当前论文高度相关"
        "——你想深入了解它的具体做法来对比；"
        "(2) 你想确认当前论文引用的某篇关键文献的真实内容和影响力；"
        "(3) 你想了解某个方法的学术谱系——它的上游（references）和下游（citations）是什么。"
        "获取的论文会存入你的参考文献工作区，后续可以随时引用。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "paper_id": {
                "type": "string",
                "description": "Semantic Scholar paper ID（从搜索结果的 URL 中可以提取）"
            },
            "doi": {
                "type": "string",
                "description": "论文的 DOI（如 '10.1093/qje/qjab042'）"
            },
            "title": {
                "type": "string",
                "description": "论文标题（会先搜索再获取详情，准确度略低于 paper_id/doi）"
            },
            "reason": {
                "type": "string",
                "description": "你为什么要深入了解这篇论文——保持意图清晰"
            }
        },
        "required": ["reason"]
    }
})

_reg({
    "name": "read_reference",
    "description": (
        "阅读用户提供的参考文献的具体内容。当用户提供了参考文档（PDF/Markdown）时，"
        "你可以用这个工具深入阅读它们的具体章节——就像你手边有一摞参考论文可以随时翻阅。"
        "典型使用场景：(1) 用户提供了一篇相关论文让你对比方法论差异；"
        "(2) 用户提供了领域综述让你了解背景；"
        "(3) 你需要确认当前论文的某个 claim 是否与参考文献一致。"
        "不指定 ref_id 时列出所有可用参考文献；不指定 section 时列出该文献的所有 sections。"
        "支持 offset 续读长内容。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ref_id": {
                "type": "string",
                "description": "参考文献 ID（如 'ref_1', 'ref_2'），不指定则列出所有可用参考文献"
            },
            "section": {
                "type": "string",
                "description": "要读取的 section 名称（如 'abstract', 'methodology', 'full'），不指定则列出该文献的所有 sections"
            },
            "offset": {
                "type": "integer",
                "description": "从第几个字符开始读取（用于续读长 section），默认 0"
            },
            "max_chars": {
                "type": "integer",
                "description": "单次最多返回字符数，默认 3000"
            }
        },
        "required": []
    }
})

# --- Findings/记录 ---

_reg({
    "name": "update_findings",
    "description": (
        "记录你发现的**问题**——论文中的漏洞、不一致、overclaim、方法论缺陷、缺失的检验。"
        "这不是笔记工具（不要用它总结'论文说了什么'），而是审稿意见记录器。"
        "每条 finding 应该是一个可以写进 reviewer report 的具体批评或疑问。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "finding": {
                "type": "string",
                "description": (
                    "你发现的问题是什么？格式：[问题类型] 具体描述。"
                    "例如：'[Overclaim] Abstract 声称 SOTA 但 Table 2 显示低于 BaselineX'、"
                    "'[方法论缺陷] DID 的平行趋势假设未检验'、"
                    "'[数据不一致] Section 3 说 N=1000 但 Table 1 只有 N=856'"
                )
            },
            "evidence": {
                "type": "string",
                "description": (
                    "原文证据：直接引用论文中支撑此判断的具体文字/数据/表述。"
                    "如果是数据不一致，引用两处矛盾的原文。如果是方法论缺陷，引用作者的描述。"
                )
            },
            "section": {
                "type": "string",
                "description": "证据来自哪个 section（方便回溯）"
            },
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "这个发现的重要程度"
            },
            "status": {
                "type": "string",
                "enum": ["verified", "needs_verification", "suggestion"],
                "description": (
                    "verified=你已确认这确实是个问题（有充分证据）; "
                    "needs_verification=你怀疑有问题但还需要读更多内容确认; "
                    "suggestion=不是错误但可以改进。"
                    "注意：'我理解了论文的claim'不是verified——"
                    "verified意味着你验证了一个**问题**确实存在。"
                )
            }
        },
        "required": ["finding", "priority", "status"]
    }
})

# --- 编辑工具 ---

_reg({
    "name": "edit_section",
    "description": (
        "修改论文的某个部分。当你确认了问题且修改方向明确时使用——先审后改，改时附上原因。"
        "修改后你应该意识到自己对这段内容有了'编辑者视角'，如果是重大修改，考虑用独立视角复核。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "要修改的 section 名称"
            },
            "new_content": {
                "type": "string",
                "description": "修改后的完整 section 内容"
            },
            "reason": {
                "type": "string",
                "description": "修改原因——解释你为什么这样改、解决了什么问题（给用户和未来的你看）"
            }
        },
        "required": ["section", "new_content", "reason"]
    }
})

_reg({
    "name": "generate_edit_plan",
    "description": (
        "根据你已确认的 findings，生成一份结构化修改计划。计划会被保存，后续 edit 工具执行时可引用。"
        "调用时机：你已经完成 deep review、积累了明确的 findings、准备从'审'转到'改'。"
        "请按优先级排序——先改 must，再 should，最后 could。"
        "注意：生成计划不代表承诺每步都执行——Agent 保留跳过、合并、调整步骤的权利。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "修改步骤列表，按执行顺序排列",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_section": {
                            "type": "string",
                            "description": "目标 section 名称"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["reword", "restructure", "add_content", "remove", "verify_data"],
                            "description": "修改类型：reword=措辞调整, restructure=段落重组, add_content=补充内容, remove=删除冗余, verify_data=数据核实后修正"
                        },
                        "description": {
                            "type": "string",
                            "description": "具体修改内容的人类可读描述"
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["must", "should", "could"],
                            "description": "优先级：must=不改论文有硬伤, should=改了明显变好, could=锦上添花"
                        },
                        "finding_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "对应的 finding 索引（从 0 开始），标明这步修改解决哪些问题"
                        }
                    },
                    "required": ["target_section", "action", "description", "priority"]
                }
            },
            "estimated_scope": {
                "type": "string",
                "enum": ["局部措辞", "段落重组", "章节重写"],
                "description": "整体修改范围评估"
            },
            "rationale": {
                "type": "string",
                "description": "整体修改策略说明：为什么选择这些步骤、它们之间的逻辑关系"
            }
        },
        "required": ["steps", "estimated_scope", "rationale"]
    }
})

_reg({
    "name": "edit_paragraph",
    "description": (
        "替换指定 section 中的某个段落（按段落索引定位）。适合修改一整段——"
        "比 edit_section 精细，比 reword_sentence 宽泛。"
        "段落按双换行分割计数（从 0 开始）。如果只需改一句话，用 reword_sentence 更合适。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "目标 section 名称"
            },
            "paragraph_index": {
                "type": "integer",
                "description": "要替换的段落索引（从 0 开始，按双换行分割计数）"
            },
            "new_content": {
                "type": "string",
                "description": "替换后的段落内容"
            },
            "reason": {
                "type": "string",
                "description": "修改原因"
            }
        },
        "required": ["section", "paragraph_index", "new_content", "reason"]
    }
})

_reg({
    "name": "reword_sentence",
    "description": (
        "精确匹配并替换一个句子。你必须完整准确地给出原句（含标点），系统会在 section 中找到并替换。"
        "如果找不到精确匹配会报错——此时请先 read_section 确认原文再重试。"
        "适合微调措辞、修正表述、消除 AI 痕迹。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "目标 section 名称"
            },
            "sentence_match": {
                "type": "string",
                "description": "要替换的原句（必须精确匹配 section 中的内容，含标点）"
            },
            "new_sentence": {
                "type": "string",
                "description": "替换后的新句子"
            },
            "reason": {
                "type": "string",
                "description": "修改原因"
            }
        },
        "required": ["section", "sentence_match", "new_sentence", "reason"]
    }
})

_reg({
    "name": "insert_content",
    "description": (
        "在指定 section 的指定位置插入一个新段落。position 表示插入点（段落索引，从 0 开始），"
        "内容会插入到该位置之前。position 等于总段落数时表示在末尾追加。"
        "适合补充内容（如 robustness check、过渡段、额外论证）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "目标 section 名称"
            },
            "position": {
                "type": "integer",
                "description": "插入位置（段落索引，内容插入到该位置之前；等于段落总数时追加到末尾）"
            },
            "content": {
                "type": "string",
                "description": "要插入的段落内容"
            },
            "reason": {
                "type": "string",
                "description": "插入原因"
            }
        },
        "required": ["section", "position", "content", "reason"]
    }
})

# --- 交互/对话 ---

_reg({
    "name": "talk_to_user",
    "description": "当你需要和用户讨论、确认方向、或呈现发现时使用。用户会看到你说的话并可以回复。",
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "你想对用户说的话"
            },
            "expects_reply": {
                "type": "boolean",
                "description": "你是否需要用户回复才能继续"
            }
        },
        "required": ["message"]
    }
})

_reg({
    "name": "review_findings",
    "description": (
        "回顾和复核你已有的 findings。可以查看全部，或按优先级/状态筛选。"
        "用于：(1) 在继续审阅前检查已有发现是否有遗漏 "
        "(2) 复核某条 finding 的 evidence 是否足够支撑结论 "
        "(3) 修改之前先审视已有审稿记录。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "enum": ["all", "high", "needs_verification", "verified"],
                "description": "筛选条件：all=全部, high=高优先级, needs_verification=待验证, verified=已验证"
            }
        },
        "required": ["filter"]
    }
})

# --- 认知控制 ---

_reg({
    "name": "spawn_perspective",
    "description": (
        "发起一个独立视角来审视特定内容。这个视角有自己独立的context，不会受你已有发现的影响"
        "——就像请一个同行专家只看论文的某个方面。它的发现会自动加入你的工作记忆（标记来源视角）。"
        "适合：统计方法审查、领域新颖性判断、实验设计评估、写作可读性检查、修改后的独立复核等。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "lens": {
                "type": "string",
                "description": "子视角的身份/专长，如 'statistical_methods_expert'、'domain_novelty_reviewer'、'experimental_design_critic'、'clarity_and_writing_reviewer'"
            },
            "focus": {
                "type": "string",
                "description": "让这个视角关注什么——可以是 section 名称（如 'experiments'）、多个 section（逗号分隔）、或一段具体描述"
            },
            "question": {
                "type": "string",
                "description": "你想让这个视角回答/验证什么具体问题。越具体越好。"
            }
        },
        "required": ["lens", "focus", "question"]
    }
})

_reg({
    "name": "spawn_parallel_readers",
    "description": (
        "当你面对一篇大型论文（50+页或5+个独立section），判断"
        "'这些section各自需要独立深入审视，而且彼此间的审视互不依赖'时，"
        "用这个工具一次发起多个并行的独立深读。每个子视角独立运行、互不影响，"
        "完成后所有发现统一汇入你的工作记忆。比连续调多次 spawn_perspective 更高效"
        "——它们会真正并行执行。使用前提：(1) 你已经做过初步全局扫描，"
        "(2) 你识别出了多个互不依赖的深入调查需求，(3) 串行深读会导致信息损耗或超出预算。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "readers": {
                "type": "array",
                "description": "要并行深读的子视角列表（最多4个）",
                "items": {
                    "type": "object",
                    "properties": {
                        "lens": {
                            "type": "string",
                            "description": "子视角的专长身份，如 'econometrics_expert'、'causal_inference_specialist'"
                        },
                        "focus": {
                            "type": "string",
                            "description": "关注的 section（逗号分隔多个）"
                        },
                        "question": {
                            "type": "string",
                            "description": "这个视角需要回答的具体问题"
                        }
                    },
                    "required": ["lens", "focus", "question"]
                },
                "minItems": 2,
                "maxItems": 4
            }
        },
        "required": ["readers"]
    }
})

_reg({
    "name": "reflect_and_plan",
    "description": (
        "暂停，退后一步看全局。就像审稿人偶尔抬头想想'我到哪了？方向对吗？接下来该看什么？'"
        "——调用这个工具就是那个'抬头'的动作。系统会给你一面镜子：进度、资源、覆盖度。"
        "你只需要说一句为什么想暂停（trigger），然后看看镜子里的信息，自然地调整方向。"
        "如果你在反思中形成了新的判断，可以通过 cognitive_update 记录下来。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "trigger": {
                "type": "string",
                "description": "一句话：你为什么想暂停看看全局？（如'读了几个section想确认方向'、'发现大问题需要重新评估'）"
            },
            "current_thinking": {
                "type": "string",
                "description": "你当前的主要判断或假说（可选，帮你反思后对比思路有没有变）"
            },
            "cognitive_update": {
                "type": "object",
                "description": (
                    "如果反思后你的策略/假说/信心有变化，在这里更新。"
                    "包含: strategy(deep_investigation/breadth_scan/targeted_verification/revision_mode/synthesis), "
                    "hypotheses([{claim, confidence}]), questions(待答问题), confidence(0-1), assessment(一句话自评)。"
                    "只填你想更新的字段即可。"
                ),
                "properties": {
                    "strategy": {"type": "string"},
                    "strategy_rationale": {"type": "string"},
                    "hypotheses": {"type": "array", "items": {"type": "object"}},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "resolved_questions": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "assessment": {"type": "string"}
                }
            }
        },
        "required": ["trigger"]
    }
})

_reg({
    "name": "switch_persona",
    "description": (
        "切换你的认知人格。当你判断当前视角的工作已足够深入，需要转换到另一个视角时使用。"
        "例如：审阅完毕后切到 writer 进行修改；修改完成后切回 scholar 复审。"
        "这是你的自主决策——你决定何时切换、为什么切换。"
        "可用人格: scholar（审稿人视角）、writer（作者/修改视角）、code_reviewer（代码审查视角）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target_persona": {
                "type": "string",
                "description": "目标人格名称: 'scholar', 'writer', 或 'code_reviewer'"
            },
            "reason": {
                "type": "string",
                "description": "一句话说明为什么要切换（如'审阅完毕，准备开始修改'、'修改结束，需要复审确认质量'）"
            }
        },
        "required": ["target_persona", "reason"]
    }
})

_reg({
    "name": "switch_model",
    "description": (
        "切换当前使用的 LLM 模型。当你判断当前任务更适合另一个模型处理时使用"
        "（例如需要深度推理、需要更快响应、或需要特定能力）。"
        "仅在多模型功能启用时可用。切换会生成上下文摘要以保持连贯性，"
        "但仍有信息损失，避免频繁切换。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target_model": {
                "type": "string",
                "description": "目标模型 ID（参见 system prompt 中的可用模型列表）"
            },
            "reason": {
                "type": "string",
                "description": "切换原因（如'需要深度推理能力'、'简单任务用低成本模型'）"
            }
        },
        "required": ["target_model", "reason"]
    }
})

_reg({
    "name": "mark_complete",
    "description": "当你确认当前任务已完成时调用。系统会检查你是否还有未验证的 high-priority 发现。",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "本次工作的简短总结"
            }
        },
        "required": ["summary"]
    }
})

# --- 验证/检测工具 ---

_reg({
    "name": "detect_ai_signals",
    "description": (
        "对一段文本进行 AI 写作信号程序化检测。零 LLM 调用，纯正则+统计分析，执行极快。"
        "检测 50+ 种 AI 写作模式（英文+中文），包括：AI cliché 词汇、公式化过渡词、"
        "套话、宣传式表达、句长均匀度、词汇重复率、排比结构等。"
        "返回多维度评分和分层判定（PASS/FAIL）。"
        "适合：(1) 修改论文后验证 AI 痕迹是否消除 (2) 审阅时量化 AI 写作程度 (3) 编辑前建立 baseline。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待检测的文本。可以是单个 section 内容、一段修改后的文字、或任何需要检查 AI 痕迹的文本。"
            },
            "section": {
                "type": "string",
                "description": "可选：指定 section 名称。若未提供 text，将自动读取该 section 的已编辑内容进行检测。"
            }
        },
        "required": ["text"]
    }
})

_reg({
    "name": "verify_citations",
    "description": (
        "验证参考文献完整性和引用一致性。零 LLM 调用，纯规则解析。"
        "检查内容：(1) .bib 条目字段完整性（按 entry type 检查必需字段）"
        "(2) 引用一致性——\\cite{key} 是否都能在 .bib 中找到 "
        "(3) 孤立条目——.bib 中存在但从未被引用的条目 "
        "(4) 重复 key、短标题等格式问题。"
        "适合：论文提交前的引用健康检查、审稿时验证参考文献规范性。"
        "两种使用模式：传入 bib_content + tex_content 文本内容（推荐），"
        "或传入 project_dir 自动发现文件。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bib_content": {
                "type": "string",
                "description": ".bib 文件的文本内容。如果论文的 references 部分是 BibTeX 格式，直接传入即可。"
            },
            "tex_content": {
                "type": "string",
                "description": ".tex 文件的文本内容（含 \\cite{} 命令）。用于交叉验证引用一致性。"
            },
            "project_dir": {
                "type": "string",
                "description": "项目目录路径。传入后会自动寻找 .bib 和 .tex 文件。与 bib_content/tex_content 二选一。"
            },
            "check_orphaned": {
                "type": "boolean",
                "description": "是否报告未被引用的 .bib 条目（默认 true，条目多时可关闭减少噪音）。"
            }
        },
        "required": []
    }
})

_reg({
    "name": "recall_context",
    "description": (
        "回查之前读过但已被压缩的上下文。当你需要重新查看之前读过的 section 原文或搜索结果的完整内容时使用。"
        "系统会从外部存储中恢复完整内容。比 re-read section 更高效（不消耗额外 token 配额），适合回溯验证。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ref_id": {
                "type": "string",
                "description": "引用 ID (如 'ref_003')。在 workspace state 的「已卸载的上下文」列表中可以看到可用的 ref_id。"
            },
            "key": {
                "type": "string",
                "description": "内容标识 (如 section 名 'methodology' 或搜索 query 'DID parallel trends')。当你不记得 ref_id 时用 key 模糊匹配。"
            }
        },
        "required": []
    }
})

_reg({
    "name": "verify_stata",
    "description": (
        "对方法学问题进行 Stata 统计验证——你的计量经济学助手。"
        "当你发现论文的实证方法存在可疑之处（如 DID 没做平行趋势检验、IV 的 first-stage F 值未报告、"
        "标准误聚类层级不对、样本选择偏差未处理）时，可以用这个工具生成 .do 代码并尝试执行验证。"
        "典型使用场景：(1) 你怀疑某个因果识别策略有缺陷——让 Stata 跑一个诊断检验；"
        "(2) 表格数字和方法描述不一致——让 Stata 复现确认；"
        "(3) 关键稳健性检验被省略——生成对应 .do 代码。"
        "注意：验证结果只作为 guidance（建议），永远不会自动修改论文。"
        "如果 Stata 环境不可用，会降级为 .do 代码输出供人工执行。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "issue": {
                "type": "object",
                "description": (
                    "你发现的方法学问题。包含 id (标识符)、description (具体描述问题是什么、"
                    "为什么你怀疑它有问题)、suggestion (可选，你建议的验证方向)。"
                ),
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "问题标识符（如 'meth_1'、'robustness_check_3'）"
                    },
                    "description": {
                        "type": "string",
                        "description": "方法学问题的具体描述：问题是什么、你为什么认为它可能有缺陷"
                    },
                    "suggestion": {
                        "type": "string",
                        "description": "你建议的验证方向或具体检验方法（如 'event study plot'、'Hausman test'）"
                    }
                },
                "required": ["id", "description"]
            },
            "methods_context": {
                "type": "string",
                "description": (
                    "论文方法/数据章节的摘要或关键段落（帮助生成更准确的 .do 代码）。"
                    "如果你已经读过 methodology section，把关键的模型设定、变量定义、样本描述放在这里。"
                )
            }
        },
        "required": ["issue"]
    }
})

_reg({
    "name": "apply_skill",
    "description": (
        "执行 SkillX 技能进行精确的规则化分析检查。当你需要对论文进行结构化验证时使用"
        "——比如检查表格数据跨表一致性（TableConsistencySkill）、"
        "追踪数学公式符号定义是否前后一致（AppendixMathAuditSkill）、"
        "验证统计数字是否匹配（StatisticalValidationSkill）。"
        "这些 Skills 是精确的规则引擎，能发现你肉眼容易遗漏的数据不一致。"
        "典型使用场景：(1) 你读到表格数据，想确认同一个数字在不同表格/正文中是否一致；"
        "(2) 你看到数学推导，想检查符号定义是否从头到尾一致；"
        "(3) 你怀疑某个统计量有问题但需要精确验证。"
        "不传 skill_name 时会列出当前可用的 Skills。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "要执行的 Skill 名称。不传时列出当前 Phase 可用的所有 Skills。"
            },
            "parameters": {
                "type": "object",
                "description": "传给 Skill 的参数（可选，大多数 Skill 不需要额外参数）。"
            },
            "section_context": {
                "type": "string",
                "description": "当前正在分析的 section 文本（可选）。如果不传，系统会自动使用你最近读过的 section。"
            }
        },
        "required": []
    }
})

_reg({
    "name": "request_phase_transition",
    "description": (
        "主动请求切换认知阶段。通常阶段转换由系统自动完成，但当你明确判断应该进入下一阶段时可以主动请求。"
        "例如：你已经完成了初步扫描，想进入深度审阅；或者你发现了需要修改的问题，想进入编辑阶段。"
        "有效阶段：initial_scan（初步扫描）、deep_review（深度审阅）、editing（编辑修改）、synthesis（综合总结）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "target_phase": {
                "type": "string",
                "enum": ["initial_scan", "deep_review", "editing", "synthesis"],
                "description": "目标阶段名称。"
            },
            "reason": {
                "type": "string",
                "description": "为什么你认为应该转换到这个阶段（帮助系统判断转换是否合理）。"
            }
        },
        "required": ["target_phase"]
    }
})

_reg({
    "name": "generate_cognitive_hints",
    "description": (
        "生成针对当前论文的审稿认知提示。在你对论文形成初步判断后使用"
        "——告诉系统这是什么类型的论文、应该重点关注哪些维度、这类论文的典型弱点是什么。"
        "系统会据此调整后续的审稿策略和完成标准。通常在 initial_scan 或 deep_review 早期使用一次即可。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "paper_type_description": {
                "type": "string",
                "description": "论文类型描述（如 'DID 因果推断实证论文'、'深度学习架构创新论文'、'理论证明+实验验证混合论文'）。"
            },
            "focus_dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "应重点关注的审稿维度（如 ['识别策略的有效性', '稳健性检验的充分性', '外部有效性']）。"
            },
            "typical_weaknesses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "这类论文的典型弱点（如 ['平行趋势假设未充分检验', '样本选择偏差', '多重检验校正缺失']）。"
            },
            "verification_strategies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "建议的验证策略（如 ['检查 event study plot', '核对 first-stage F 统计量', '搜索同类方法的已知局限']）。"
            }
        },
        "required": ["paper_type_description"]
    }
})

# manage_plugins — 从 plugin_installer.py 获取（单一来源）
_reg(_MANAGE_PLUGINS_SCHEMA)


# ============================================================
# Persona 工具名称列表 — 定义每个 Persona 使用哪些工具
# ============================================================

SCHOLAR_TOOL_NAMES: list[str] = [
    "read_section",
    "search_literature",
    "fetch_paper_detail",
    "read_reference",
    "update_findings",
    "edit_section",
    "generate_edit_plan",
    "edit_paragraph",
    "reword_sentence",
    "insert_content",
    "talk_to_user",
    "review_findings",
    "spawn_perspective",
    "spawn_parallel_readers",
    "reflect_and_plan",
    "switch_persona",
    "switch_model",
    "mark_complete",
    "detect_ai_signals",
    "verify_citations",
    "recall_context",
    "verify_stata",
    "apply_skill",
    "request_phase_transition",
    "generate_cognitive_hints",
    "manage_plugins",
]

WRITER_TOOL_NAMES: list[str] = [
    "read_section",
    "update_findings",
    "edit_section",
    "detect_ai_signals",
    "talk_to_user",
    "review_findings",
    "reflect_and_plan",
    "switch_persona",
    "switch_model",
    "mark_complete",
]

CODE_REVIEWER_TOOL_NAMES: list[str] = [
    "read_section",
    "search_literature",
    "update_findings",
    "talk_to_user",
    "review_findings",
    "reflect_and_plan",
    "switch_model",
    "mark_complete",
]


# ============================================================
# Persona-specific description 覆盖
# 同一工具在不同 persona 下可以有不同的 description（引导不同行为）
# ============================================================

_WRITER_DESC_OVERRIDES: dict[str, str] = {
    "read_section": "读取论文的某个部分。你可以指定 section 名称，或 'list' 列出所有 sections。",
    "update_findings": (
        "记录你发现的写作问题——论证逻辑断裂、AI 痕迹、冗余表述、claim-evidence 不匹配。"
        "这是你的诊断记录器。"
    ),
    "edit_section": "修改论文的某个部分。先诊断后动手——确认问题存在且修改方向明确时使用。",
    "detect_ai_signals": "对文本进行 AI 写作信号检测。修改后必须验证。",
    "talk_to_user": "和用户讨论修改方向或确认重大改动。",
    "review_findings": "回顾已记录的写作问题。",
    "reflect_and_plan": "暂停看全局——我改了什么？还有什么要改？方向对吗？",
    "switch_persona": (
        "切换你的认知人格。修改告一段落后，你可以切回 scholar 视角进行复审，确认修改质量。"
        "可用人格: scholar（审稿人视角）、writer（作者/修改视角）、code_reviewer（代码审查视角）。"
    ),
    "switch_model": "切换当前使用的 LLM 模型。当你判断当前任务更适合另一个模型处理时使用。仅在多模型功能启用时可用。",
    "mark_complete": "修改任务完成。",
}

_CODE_REVIEWER_DESC_OVERRIDES: dict[str, str] = {
    "read_section": (
        "读取代码的某个部分。你可以指定文件名或模块名（如 'main.py', 'utils', 'auth_handler'），"
        "或者 'list' 列出所有可读取的代码段。对于长文件，每次返回最多 6000 字符；"
        "如果被截断，返回信息中会告诉你如何用 offset 续读。"
    ),
    "search_literature": (
        "搜索技术文档、最佳实践、已知漏洞模式。典型使用场景："
        "(1) 某个库的用法是否正确——搜索官方文档确认；"
        "(2) 某个设计模式是否有已知陷阱——搜索相关讨论；"
        "(3) 某个安全实践是否符合当前标准——搜索 OWASP/CWE 等。"
    ),
    "update_findings": (
        "记录你发现的代码问题——安全漏洞、逻辑错误、性能隐患、架构缺陷、可维护性问题。"
        "这不是笔记工具（不要用它总结'代码做了什么'），而是 code review 意见记录器。"
    ),
    "talk_to_user": "当你需要和用户讨论设计决策、确认需求、或呈现审阅结论时使用。",
    "review_findings": "回顾已记录的代码问题。可以查看全部，或按优先级/状态筛选。",
    "reflect_and_plan": "暂停，退后一步看全局。确认审阅方向、覆盖度、资源消耗。",
    "switch_model": "切换当前使用的 LLM 模型。当你判断当前任务更适合另一个模型处理时使用。仅在多模型功能启用时可用。",
    "mark_complete": "代码审阅完成。系统会检查你是否还有未验证的 high-priority 发现。",
}


# ============================================================
# Property-level description overrides（参数描述定制）
#
# 同一工具的 input_schema 结构（字段名、类型、required）各 persona 完全一致，
# 但每个 property 的 description 可按 persona 定制，引导 LLM 在不同角色语境下
# 给出更贴切的参数值。
#
# 格式: { "tool_name": { "property_name": "定制描述" } }
# ============================================================

_WRITER_PROP_OVERRIDES: dict[str, dict[str, str]] = {
    "read_section": {
        "offset": "从第几个字符开始读取（用于续读被截断的长 section）",
    },
    "update_findings": {
        "finding": (
            "你发现的写作问题。格式：[问题类型] 具体描述。例如："
            "'[逻辑断裂] Introduction 第3段的研究缺口和第4段的贡献声明之间缺少过渡'、"
            "'[AI痕迹] Abstract 使用了 furthermore/moreover 等公式化连接词'"
        ),
        "evidence": "原文证据",
        "section": "问题所在 section",
        "priority": "重要程度",
        "status": "verified=确认需要改; needs_verification=需要读更多确认; suggestion=可改可不改",
    },
    "edit_section": {
        "reason": "修改原因——解释你为什么这样改、解决了什么问题",
    },
    "detect_ai_signals": {
        "text": "待检测的文本",
    },
    "talk_to_user": {
        "expects_reply": "是否需要用户回复",
    },
    "review_findings": {
        "filter": "筛选条件",
    },
    "reflect_and_plan": {
        "trigger": "为什么想暂停",
        "current_thinking": "当前判断",
        "cognitive_update": "策略/假说更新",
    },
    "switch_persona": {
        "reason": "一句话说明为什么要切换",
    },
    "switch_model": {
        "reason": "切换原因",
    },
    "mark_complete": {
        "summary": "本次修改的总结",
    },
}

_CODE_REVIEWER_PROP_OVERRIDES: dict[str, dict[str, str]] = {
    "read_section": {
        "section": "要读取的代码段名称（文件名/模块名），或 'list' 列出所有可用段",
        "offset": "从第几个字符开始读取（用于续读被截断的长文件）",
    },
    "search_literature": {
        "query": "搜索查询词（英文效果最好）",
        "reason": "你为什么要搜索这个——保持意图清晰",
    },
    "update_findings": {
        "finding": (
            "你发现的问题。格式：[问题类型] 具体描述。例如："
            "'[安全漏洞] auth_handler.py L23 的 SQL 拼接未做参数化，存在注入风险'、"
            "'[竞态条件] worker.py 的 task_queue 在多线程下无锁保护'、"
            "'[性能] search() 在循环内重复创建数据库连接'"
        ),
        "evidence": "代码证据：直接引用相关代码片段。",
        "section": "问题所在的文件/模块名",
        "priority": "high=阻断性（安全/正确性）, medium=设计问题, low=代码质量",
        "status": "verified=确认存在; needs_verification=需要看更多代码确认; suggestion=改进建议",
    },
    "review_findings": {
        "filter": "筛选条件",
    },
    "reflect_and_plan": {
        "trigger": "为什么想暂停看全局",
        "current_thinking": "当前的主要判断",
        "cognitive_update": "策略/假说更新",
    },
    "switch_model": {
        "reason": "切换原因",
    },
    "mark_complete": {
        "summary": "本次审阅的总结：整体评价 + 关键发现摘要",
    },
}


# ============================================================
# 公共 API — 构建工具列表
# ============================================================

def get_tools_for_persona(persona: str) -> list[dict[str, Any]]:
    """返回指定 Persona 的完整工具 schema 列表。

    根据 persona 名称获取对应的工具名称列表，从 TOOL_REGISTRY 中
    取出完整 schema，并应用 persona-specific 的覆盖：
      - 工具顶层 description（角色化的工具说明）
      - input_schema 中各 property 的 description（参数级文案定制）

    不可变性保证：
      - 有 override 的工具：返回浅拷贝副本，修改返回值不会影响 TOOL_REGISTRY
      - 无 override 的工具（如 Scholar 全部）：直接返回 TOOL_REGISTRY 中的原始引用
        （性能优化；调用者不应修改返回值）

    Args:
        persona: "scholar", "writer", 或 "code_reviewer"

    Returns:
        该 persona 可用的工具 schema 列表。有 override 的是独立副本，
        无 override 的是 registry 原始引用（只读）。

    Raises:
        KeyError: persona 名称无效
        ValueError: 某个工具名在 TOOL_REGISTRY 中不存在
    """
    name_map: dict[str, list[str]] = {
        "scholar": SCHOLAR_TOOL_NAMES,
        "writer": WRITER_TOOL_NAMES,
        "code_reviewer": CODE_REVIEWER_TOOL_NAMES,
    }
    desc_override_map: dict[str, dict[str, str]] = {
        "scholar": {},
        "writer": _WRITER_DESC_OVERRIDES,
        "code_reviewer": _CODE_REVIEWER_DESC_OVERRIDES,
    }
    prop_override_map: dict[str, dict[str, dict[str, str]]] = {
        "scholar": {},
        "writer": _WRITER_PROP_OVERRIDES,
        "code_reviewer": _CODE_REVIEWER_PROP_OVERRIDES,
    }

    if persona not in name_map:
        raise KeyError(f"Unknown persona: {persona!r}. Valid: {list(name_map.keys())}")

    tool_names = name_map[persona]
    desc_overrides = desc_override_map[persona]
    prop_overrides = prop_override_map[persona]
    tools: list[dict[str, Any]] = []

    for name in tool_names:
        if name not in TOOL_REGISTRY:
            raise ValueError(
                f"Tool '{name}' listed in {persona} persona but not found in TOOL_REGISTRY. "
                f"Did you forget to register it?"
            )
        schema = TOOL_REGISTRY[name]
        needs_copy = name in desc_overrides or name in prop_overrides

        if needs_copy:
            # 构建定制化的 schema 副本（不修改原始 registry）
            schema = {**schema}
            if name in desc_overrides:
                schema["description"] = desc_overrides[name]
            if name in prop_overrides:
                # 深拷贝 input_schema：对每个 property dict 做浅拷贝以确保
                # 修改返回结果不会污染 TOOL_REGISTRY 原始数据
                old_input_schema = schema["input_schema"]
                new_properties = {}
                for prop_name, prop_def in old_input_schema["properties"].items():
                    if prop_name in prop_overrides[name]:
                        new_properties[prop_name] = {
                            **prop_def,
                            "description": prop_overrides[name][prop_name],
                        }
                    else:
                        # 浅拷贝：防止外部修改影响 registry
                        new_properties[prop_name] = {**prop_def}
                schema["input_schema"] = {
                    **old_input_schema,
                    "properties": new_properties,
                }

        tools.append(schema)

    return tools


def validate_registry() -> None:
    """启动时校验：确认所有 persona 引用的工具名都在 registry 中存在。

    Raises:
        ValueError: 如果发现引用了不存在的工具名
    """
    all_persona_names = {
        "scholar": SCHOLAR_TOOL_NAMES,
        "writer": WRITER_TOOL_NAMES,
        "code_reviewer": CODE_REVIEWER_TOOL_NAMES,
    }
    errors: list[str] = []
    for persona, names in all_persona_names.items():
        for name in names:
            if name not in TOOL_REGISTRY:
                errors.append(f"  {persona}: '{name}' not in TOOL_REGISTRY")
    if errors:
        raise ValueError(
            "Tool schema registry validation failed:\n" + "\n".join(errors)
        )


# 启动时自动校验
validate_registry()
