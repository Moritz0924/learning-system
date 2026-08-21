from __future__ import annotations


def task_copy(locale: str, task_type: str, node_code: str) -> tuple[str, str] | None:
    if task_type not in {"study", "review", "practice"}:
        return None
    if locale == "zh-CN":
        node = node_code.replace("_", " ")
        if task_type == "study":
            return f"学习 {node}", f"掌握 {node} 的基础知识。"
        if task_type == "review":
            return f"复习 {node}", "继续学习前，复习薄弱知识点。"
        return f"练习 {node}", f"通过简短练习应用 {node}。"
    if task_type == "study":
        return f"Study {node_code}", f"Build confidence on {node_code.replace('_', ' ')}."
    if task_type == "review":
        return f"Review {node_code}", "Review weak knowledge area before continuing."
    return f"Practice {node_code}", f"Apply {node_code.replace('_', ' ')} in a short practice task."
