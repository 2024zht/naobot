# naobot

基于 Lagrange.Milky、NoneBot 2 和 DeepSeek 的 QQ 群机器人。默认只处理一个指定群，包含 AI 问答、关键词回复、表情包、EmojiMix、群管理和反诈防护。

## 功能

- `@机器人 你的问题`：直接使用 DeepSeek AI 问答，并清理 QQ 无法渲染的 Markdown
- `@机器人 关键词`：持久化关键词回复
- `@机器人 表情包制作`、`😂+🥺`：表情模板与 EmojiMix
- 新成员欢迎
- `@机器人 禁言`、`@机器人 踢出`、`@机器人 撤回`：管理员群管理
- 文字规则、图片 OCR、二维码、QQ 名片和重要通知防护
- 违规累计 3 次自动踢出
- `@机器人 添加违规`：由管理员从违规原文中提取高风险词并加入持久化黑名单

完整的功能、命令、权限、数据文件和当前限制见 [FEATURES.md](FEATURES.md)。新增、修改或删除功能时必须同步维护该文件。

## Docker 部署

1. 从示例创建本地配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`，至少填写 QQ 号、测试群号、Milky Token 和 DeepSeek API Key。生成随机 Milky Token：

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. 构建并启动：

   ```powershell
   docker compose up -d --build
   docker compose logs -f lagrange-milky
   ```

4. 首次启动按 Lagrange 日志提示登录 QQ。登录状态保存在 `lagrange-data/`，机器人数据保存在 `data/`。

5. 将机器人设为目标群管理员。`NAO_ADMIN_QQ_IDS` 留空时，群主和群管理员可以使用管理命令；填写后仅允许列出的 QQ 号，多个号码用英文逗号分隔。

停止服务：

```powershell
docker compose down
```

## 管理命令

```text
@nao 反诈状态
@nao 反诈记录 @成员
@nao 清除违规 @成员
@nao 添加违规 违规成员发送的内容
@nao 添加违规                  # 回复违规消息后发送
@nao 违规词列表
@nao 删除违规词 词条
@nao 禁言 @成员 [分钟]
@nao 踢出 @成员
@nao 撤回                      # 回复需要撤回的消息后发送
```

违规词保存在 `data/fraud_keywords.json`。AI 只生成候选词，本地代码会拒绝原文中不存在的词和“微信、论文、通知”等过宽词；DeepSeek 请求失败时使用确定性规则提取。

## 本地测试

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

## 安全

`.env`、QQ 登录状态、持久化数据和缓存均已加入 `.gitignore`。不要把真实 API Key、Milky Token、服务器密码或 `lagrange-data/` 提交到仓库。
