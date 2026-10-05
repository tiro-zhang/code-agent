"""逐项记录本轮清单范围，保留旧章节的历史复选框。"""

from pathlib import Path
import re


REPO = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
EVIDENCE = [
    "test_hook_config / conditions / events / matching；pytest.txt",
    "test_hook_lifecycle / loop / skills；pytest.txt",
    "test_hook_loop；denial-and-alternative.txt / request-audit.json",
    "test_hook_actions；timeout-result.txt / cancel-verified.json",
    "test_hook_runtime / lifecycle；request-audit.json",
    "test_hook_prompt；request-audit.json",
    "test_hook_scheduling / lifecycle；final-plan-result.txt / control-cancel-audit.json",
    "test_hook_terminal；final-startup-approval.txt / control-running.txt",
    "test_terminal_projection / ui_keys；background-notice-draft.txt / f2-after-control.txt / status-after-control.txt",
    "formatting-result.txt / denial-and-alternative.txt / request-audit.json",
    "timeout-result.txt / cancel-verified.json / after-cancel-continue.txt / final-plan-result.txt",
    "control-approval-with-notice.txt / control-cancelling.txt / plain-pty-output.txt / plain-exit-audit.json",
    "pytest.txt / build.txt / openspec.txt / review.txt / request-audit.json",
    "README.md 排除项及未执行范围；test_hook_actions / runtime / config",
]


def main():
    section = ""
    hook_index = 0
    historical = 0
    output = [
        "# checklist 逐项本轮审计（2026-10-05）",
        "",
        "历史复选框只表示原章节当时结果；本表的状态表示本次 add-lifecycle-hooks 的实际验收。",
        "旧条目未逐一重复其完整独立场景（包括日期、测试数及远端配置），逐项记录未执行；",
        "本次完整 1123 项回归覆盖相关代码，但不借此重写旧场景的真实验收结论。",
        "Hook 本轮条目同时使用自动化与真实 tmux，具体区分见 README.md。",
        "",
        "| 原清单行号 | 章节 | 原复选框 | 本轮状态 | 条目 | 本轮证据／未执行原因 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for number, line in enumerate((REPO / "checklist.md").read_text().splitlines(), 1):
        if line.startswith("## "):
            section = line[3:]
        item = re.fullmatch(r"- \[([ x])\] (.+)", line)
        if not item:
            continue
        mark, text = item.groups()
        if section.startswith("生命周期 Hook"):
            assert mark == "x"
            status = "通过"
            reason = EVIDENCE[hook_index]
            hook_index += 1
        else:
            status = "未执行"
            reason = "未逐一重复该历史条目的完整真实场景；本次完整回归见 pytest.txt，历史证据不代替本轮。"
            historical += 1
        escape = lambda value: value.replace("|", "\\|")
        output.append(f"| {number} | {escape(section)} | [{mark}] | {status} | {escape(text)} | {escape(reason)} |")
    assert hook_index == len(EVIDENCE) == 14
    output.extend(["", f"本轮 Hook：14 项通过；历史独立场景：{historical} 项未重复执行。"])
    (FOLDER / "checklist-audit.md").write_text("\n".join(output) + "\n")
    print(f"Hook 14 项通过；历史 {historical} 项未重复执行")


if __name__ == "__main__":
    main()
