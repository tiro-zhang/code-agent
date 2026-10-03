"""权限 shell 检查只分析字符串，不执行任何命令。"""

import importlib

import pytest

from mewcode.tools.base import ToolError


def shell():
    return importlib.import_module("mewcode.permissions.shell")


@pytest.mark.parametrize("command", [
    "git status", "git 'push'", 'printf "%s" "a | b"',
    "printf '%s' '*'", r"printf \*", "MODE=prod git status",
])
def test_static_simple_commands_can_use_glob_allow(command):
    assert shell().analyze_command(command).simple is True


def test_static_quotes_cannot_hide_command_from_deny_matching():
    analysis = shell().analyze_command("git status && MODE=prod g'it' 'push'")
    assert analysis.simple is False
    assert analysis.subjects[0] == "git status && MODE=prod g'it' 'push'"
    assert "git status" in analysis.subjects
    assert "MODE=prod g'it' 'push'" in analysis.subjects
    assert "git push" in analysis.subjects
    assert len(analysis.subjects) == len(set(analysis.subjects))


@pytest.mark.parametrize("command, visible", [
    ("git status | cat", "cat"),
    ("git status || git push", "git push"),
    ("git status; git push", "git push"),
    ("git status\ngit push", "git push"),
    ("git status & git push", "git push"),
    ("(git status && git push)", "git push"),
    ('printf "%s" "$(git push)"', "git push"),
    ("printf '%s' `git push`", "git push"),
    ('printf "%s" "$(printf \'%s\' \"$(git push)\")"', "git push"),
    ("git status > output.txt", "git status"),
    ("cat < input.txt", "cat"),
])
def test_complex_structure_exposes_every_explicit_command(command, visible):
    analysis = shell().analyze_command(command)
    assert analysis.simple is False
    assert visible in analysis.subjects


@pytest.mark.parametrize("command", [
    "git $ACTION", "git ${ACTION}", "printf '%s' ~", "cat *.py",
    "cat a?.py", "cat [ab].py", "MODE=$MODE git status",
])
def test_dynamic_words_need_exact_rule_or_user_grant(command):
    assert shell().analyze_command(command).simple is False


@pytest.mark.parametrize("command", [
    "", "   ", "git status &&", "printf 'unterminated", "echo \0",
    "echo $((1+2))", "echo ${VALUE:-$(git push)}", "echo ${VALUE#prefix}",
    "cat <<EOF\n$(git push)\nEOF\n", "cat <<'EOF'\nliteral\nEOF\n",
    "cat <(git push)", "if true; then git push; fi",
    "for x in a; do echo $x; done", "f() { git push; }; f",
])
def test_incomplete_or_unsupported_analysis_fails_closed(command):
    with pytest.raises(ToolError) as caught:
        shell().analyze_command(command)
    assert caught.value.code == "permission_check_failed"
    assert caught.value.details["not_started"] is True


@pytest.mark.parametrize("command", [
    "rm -rf /", "rm -fr -- /", "rm --recursive --force /",
    "rm / -rf", "sudo rm -rf /*", "rm -r ~/", "rm -rf ~/*",
    "rm -rf $HOME", 'rm -rf "${HOME}/"', "rm -rf /Users/alice",
    "rm -rf /home/alice/", "rm -rf /root", "rm -rf /./", "rm -rf /./*",
    'rm -rf "${HOME}/."', "rm -rf /Users/alice/.",
    "mkfs.ext4 /dev/sda", "wipefs -a /dev/sda",
    "diskutil eraseDisk APFS name /dev/disk2", "dd if=input of=/dev/rdisk2",
    "printf data > /dev/sda", "tee /dev/sda", "diskutil secureErase 0 /dev/disk2",
    ":(){ :|:& };:", "bomb() { bomb | bomb & }; bomb",
])
def test_blacklist_denies_known_dangerous_text_without_execution(command):
    match = shell().check_blacklist(command)
    assert match is not None
    identifier, reason = match
    assert identifier and reason
    with pytest.raises(ToolError) as caught:
        shell().analyze_command(command)
    assert caught.value.code == "permission_denied"
    assert caught.value.details == {
        "source": "blacklist", "rule_id": identifier, "not_started": True,
    }


@pytest.mark.parametrize("command", [
    "rm -f file.txt", "rm -rf build/", "rm -rf /tmp/mewcode-test",
    "rm -rf ~/project/build", "echo mkfs.ext4", "git status",
    "dd if=/dev/zero of=output.bin", "printf data > /dev/null",
    "printf data > /dev/stdout", "printf data > /dev/fd/1",
])
def test_blacklist_keeps_common_safe_operations_available(command):
    assert shell().check_blacklist(command) is None


def test_static_quote_removal_rechecks_blacklist():
    with pytest.raises(ToolError) as caught:
        shell().analyze_command("r'm' '-rf' '/'")
    assert caught.value.code == "permission_denied"
    assert caught.value.details["source"] == "blacklist"


def test_raw_blacklist_precedes_parse_failure():
    with pytest.raises(ToolError) as caught:
        shell().analyze_command("rm -rf / && (")
    assert caught.value.code == "permission_denied"
    assert caught.value.details["source"] == "blacklist"


@pytest.mark.parametrize("command", [
    "echo $(git status\ngit push)",
    "echo $(git status # comment\ngit push)",
    "echo $(git status\n# comment\ngit push)",
    "echo `git status\ngit push`",
    "echo `git status # comment\ngit push`",
])
def test_partial_nested_ast_is_not_accepted_as_complete(command):
    # bashlex 0.18 可能只返回命令替换的第一行，节点范围也没有包含右括号。
    with pytest.raises(ToolError) as caught:
        shell().analyze_command(command)
    assert caught.value.code == "permission_check_failed"
    assert caught.value.details["not_started"] is True


def test_escaped_newline_cannot_hide_static_deny_subject():
    assert "git push" in shell().analyze_command("g\\\nit p\\\nush").subjects


def test_single_quoted_substitution_text_is_not_a_nested_command():
    analysis = shell().analyze_command("printf '%s' '$(git push)'")
    assert analysis.simple is True
    assert "git push" not in analysis.subjects


def test_redirection_substitution_is_traversed_before_any_execution():
    analysis = shell().analyze_command("git status > $(git push)")
    assert analysis.simple is False
    assert "git push" in analysis.subjects


def test_comments_do_not_hide_later_top_level_commands():
    analysis = shell().analyze_command("git status # comment\n# second comment\ngit push")
    assert analysis.simple is False
    assert "git push" in analysis.subjects
