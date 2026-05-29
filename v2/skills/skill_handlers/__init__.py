"""
skills/skill_handlers/ — 操作型 Skill 的 Handler 实现目录

V4 Phase D1: 此目录存放所有操作型 Skill 的 Python handler 函数。

Handler 约定:
    - 每个文件导出一个或多个 handler 函数
    - 统一签名: def handler_name(args: dict, state: Any) -> str
    - args: LLM tool call 传入的参数字典
    - state: WorkspaceState 对象（提供论文内容、findings 等上下文）
    - 返回值: 字符串，作为 tool 执行结果返回给 LLM

安全约束:
    - Handler 只能读取 state，不应修改系统级状态（修改 findings 等应通过标准 tool 路径）
    - Handler 不应进行网络请求或文件系统写操作（除写入 .workspace/ 目录）
"""
