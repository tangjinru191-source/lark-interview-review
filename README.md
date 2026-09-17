# lark-interview-review

把本地面试录音上传到**自己的飞书云盘与飞书妙记**，获取原始逐字稿，再由 Codex 完成逐题复盘、评分和低分题推荐回答。转写只使用飞书妙记，不调用其他语音识别服务。

## 安装

1. 安装 Codex、Python 3 和 `lark-cli`，确保 `python3 --version` 与 `lark-cli --version` 可运行。
2. 将本仓库复制到用户 Skill 目录，例如 `~/.codex/skills/lark-interview-review`。目录中应直接包含 `SKILL.md`、`scripts/`、`references/` 和 `agents/`。
3. 重启 Codex 或新开对话，使用 `$lark-interview-review` 并提供面试录音。

首次使用需要在本机执行 `lark-cli config init --new` 配置可访问的飞书应用，并由录音所有者在飞书页面完成 OAuth 授权。Skill 会检查所需权限并引导授权；已有有效登录态时直接复用。授权失效、撤销或新增权限后才需要再次授权。**Skill 不包含任何应用密钥、账号凭证或录音样例。**

所需用户权限：`drive:file:upload`、`minutes:minutes.upload:write`、`minutes:minutes.basic:read`、`minutes:minutes.artifacts:read`。这些权限还须在所使用的飞书应用及租户中开放。用户应核对授权页面展示的应用与权限；不应为了免授权改用机器人身份。

## 使用

```text
$lark-interview-review 请转写这段面试录音，给我原始逐字稿、全部问答、评分和低分题推荐回答。
```

也可以在 Skill 目录中单独检查授权状态：

```bash
python3 scripts/transcribe_interview.py --auth-check-only
```

脚本默认把转写产物写入当前目录下的 `interview-review-output/`；建议使用 `--output-dir` 指向仓库外的私人目录。不要把录音、逐字稿、`result.json` 或授权二维码提交到 GitHub。

## 隐私边界

- 源录音会上传到执行者授权的飞书账号；不会上传到这个 GitHub 仓库。
- 逐字稿和复盘属于敏感个人材料，应仅在执行者指定的本地目录保存。
- 本仓库只包含通用工作流、脚本和评分规范。GitHub 仓库所有者的账号名仍会公开显示。
