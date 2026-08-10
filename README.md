# naobot

基于 Lagrange.Milky、NoneBot 2 和 DeepSeek 的 QQ 群机器人。默认只处理一个指定群，包含 AI 问答、关键词回复、表情包、EmojiMix、群管理和反诈防护。

## 功能

- `@机器人 你的问题`：DeepSeek 在一次请求中生成纯文字回答和隐藏的场景判断，机器人只从同场景的已标注素材中选择表情
- 小火人群聊模式：无需 `@`，DeepSeek 会在明显有梗或适合接话时用一到三句主动回应，遇到近期或陌生梗时可通过官方 `web_search` 联网核实；原 `@机器人` 模式保留
- `@机器人 关键词`：持久化关键词回复
- `@机器人 表情包制作`、`😂+🥺`：表情模板与 EmojiMix
- `@机器人 今日人品`、`@机器人 猜成语`、`@机器人 人生重开`：群内趣味功能
- `@机器人 猜人物`：由 DeepSeek 通过最多 20 个问题猜测人物
- 新成员欢迎
- `@机器人 禁言`、`@机器人 踢出`、`@机器人 撤回`：管理员群管理
- `@机器人 定时`、`@机器人 定时列表`、`@机器人 取消定时`：管理员设置和管理群提醒
- 文字规则、图片 OCR、二维码、QQ 名片和重要通知防护
- 违规累计 3 次自动踢出
- `@机器人 添加违规`：由管理员从违规原文中提取高风险词并加入持久化黑名单

完整的功能、命令、权限、数据文件和当前限制见 [FEATURES.md](FEATURES.md)。新增、修改或删除功能时必须同步维护该文件。

## Docker 部署

1. 从示例创建本地配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 编辑 `.env`，至少填写 QQ 号、测试群号、Lagrange 签名 Token、Milky Token 和 DeepSeek API Key。签名 Token 与 Milky Token 用途不同；生成随机 Milky Token：

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

机器人会自动生成 `data/reaction_catalog.json`。参考 [reaction_catalog.example.json](examples/reaction_catalog.example.json) 为素材填写 `scenes`；保存后下一次 AI 回复即会读取新标签，不需要重建镜像。

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
@nao 定时 2026-08-10 21:00 提交作业
@nao 定时任务，本周五 09:00 提醒部署网站
@nao 每天晚上九点提醒我写 donelist
@nao 定时列表
@nao 取消定时 任务编号
```

管理员可以直接用自然语言要求 nao 创建一次性、每天或每周提醒，DeepSeek 会通过受约束的定时工具提取下一次时间、重复周期和内容；也可以用 `定时任务，...` 明确进入智能定时。定时提醒使用北京时间，不写具体时刻时默认 `09:00`，到点后机器人会在原群 `@` 创建者并发送提醒。任务保存在 `data/reminders.sqlite3`，容器重启后不会丢失。

违规词保存在 `data/fraud_keywords.json`。AI 只生成候选词，本地代码会拒绝原文中不存在的词和“微信、论文、通知”等过宽词；DeepSeek 请求失败时使用确定性规则提取。

## 本地测试

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python -m pip install --no-deps -r requirements-plugins-no-deps.txt
.\.venv\Scripts\python -m pytest -q
```

## 安全

`.env`、QQ 登录状态、持久化数据和缓存均已加入 `.gitignore`。不要把真实 API Key、Lagrange 签名 Token、Milky Token、服务器密码或 `lagrange-data/` 提交到仓库。
