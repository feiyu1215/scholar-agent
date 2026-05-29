"""Quick test: verify _detect_finding_overlaps catches the E2E duplicate findings."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.harness import _detect_finding_overlaps

findings = [
    {
        "finding": "[Assumption Boundary] 核心识别假设为患者对放射科医生的 quasi-random assignment，作者通过控制时间和地点因素以及对患者特征做 balance test来验证。虽然在44个station上年龄等特征平衡，但整体样本仍有小但显著的不平衡。需要进一步追问：这种不平衡是否会导致核心估计量偏误？作者主要依赖时间和station controls，未对所有潜在未观测混淆做充分讨论。",
        "priority": "high",
        "status": "needs_verification",
    },
    {
        "finding": "[方法论缺陷] quasi-random assignment 虽然在流程上有较强的随机性，但 station 间组织流程不同、患者回访依赖于 VHA 系统，可能导致 assignment 不完全随机，balance test未必能捕捉所有混淆变量。",
        "priority": "high",
        "status": "needs_verification",
    },
    {
        "finding": "[理论检验] 作者对 judges design 的 strict monotonicity 假设进行了严格理论推导，并提出 average monotonicity、probabilistic monotonicity、skill-propensity independence 等弱化条件。",
        "priority": "high",
        "status": "verified",
    },
]

overlaps = _detect_finding_overlaps(findings)
print(f"Overlaps detected: {len(overlaps)}")
for o in overlaps:
    print(f"  {o}")

# Verify: findings 1 and 2 should overlap, but not 1&3 or 2&3
assert len(overlaps) >= 1, f"Expected at least 1 overlap, got {len(overlaps)}"
assert "#1" in overlaps[0] and "#2" in overlaps[0], f"Expected overlap between #1 and #2, got: {overlaps[0]}"
print("\n✅ Overlap detection works correctly!")
