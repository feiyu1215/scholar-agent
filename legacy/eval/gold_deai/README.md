# De-AI Gold Test Set

> 用于评测 `deai_engine.py` 去 AI 审计/修复效果的标准测试集。

## 设计原则

1. **Coverage**: 覆盖 S_GENERAL (12规则), S1 (CS), S3 (Economics) 三个场景的主要信号类型
2. **Paired**: 每组包含 `ai_text` (AI生成/改写文本) 和 `human_reference` (人工去AI后的目标)
3. **Multi-dimensional**: 每组标注主要信号类型，便于分维度追踪分数变化
4. **Graduated**: 包含 easy (单一信号) 和 hard (多信号叠加) 两种难度

## Case Schema

```json
{
  "id": "deai_gold_001",
  "scene": "S1",
  "difficulty": "easy|medium|hard",
  "primary_signals": ["AI_VOCABULARY", "TRICOLON"],
  "secondary_signals": [],
  "description": "描述这个 case 测试什么",
  "ai_text": "包含 AI 痕迹的文本（200-400 words）",
  "human_reference": "人工去 AI 后的期望目标文本",
  "signal_annotations": [
    {
      "signal_type": "AI_VOCABULARY",
      "sentence": "具体哪句触发",
      "expected_action": "replace 'delve' with 'investigate'"
    }
  ],
  "metadata": {
    "source": "手工构造|真实论文改编",
    "field": "computer_science|economics"
  }
}
```

## 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| detection_recall | 25% | 标注的信号被检出的比例 |
| detection_precision | 25% | 检出的信号中确实是 AI 的比例 |
| fix_quality | 25% | 修复后文本与 human_reference 的语义/风格相似度 |
| voice_preservation | 25% | 修复是否保持了原文的学术语域和论点 |

## 运行方式

```bash
python -m eval.run_deai_gold                    # 全量跑
python -m eval.run_deai_gold --scene S1         # 只跑 S1 场景
python -m eval.run_deai_gold --signal TRICOLON  # 只跑含特定信号的 case
```
