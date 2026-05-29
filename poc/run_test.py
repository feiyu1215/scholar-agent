"""
run_test.py — 非交互式运行 PoC 测试

用法：python3 poc/run_test.py
"""
from __future__ import annotations
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from cognitive_loop import cognitive_loop, WorkspaceState


async def main():
    paper_path = str(Path(__file__).resolve().parent / "test_paper.md")
    
    print(f"加载论文: {paper_path}")
    workspace = WorkspaceState(paper_path)
    print(f"   加载了 {len(workspace.paper_sections)} 个 sections")
    
    user_message = "帮我看看这篇论文有什么问题，我准备投 NeurIPS，给我你的专业判断。"
    print(f"\n用户: {user_message}\n")
    print("=" * 60)
    
    # 运行认知循环
    result = await cognitive_loop(
        user_message=user_message,
        workspace=workspace,
        verbose=True,
    )
    
    # 输出最终结果
    print("\n" + "=" * 60)
    print("最终状态:")
    print(f"   轮次: {workspace.turn_count}")
    print(f"   发现: {len(workspace.findings)} 条")
    print(f"   修改: {len(workspace.edits)} 处")
    print(f"   Tokens: ~{workspace.total_tokens_used}")
    if workspace.findings:
        print("\n   发现列表:")
        for f in workspace.findings:
            icon = {"high": "[高优]", "medium": "[中优]", "low": "[低优]"}[f["priority"]]
            print(f"     {icon} [{f['status']}] {f['finding']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
