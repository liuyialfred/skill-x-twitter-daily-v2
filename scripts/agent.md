# x-twitter-daily-v2 — Scheduler Agent

此 agent 由 OpenClaw cron 每天早上 08:30 (UTC+8) 触发，执行完整的 x-twitter-daily 工作流。

## 任务描述

执行 `x-twitter-daily-v2` 工作流：抓取 X 科技推文 → 翻译 → 话题聚类 → 输出 JSON → 微信推送。

## 执行步骤

### Step 1: 检查 Cookie 有效性
查看 `~/.openclaw/workspace/twitter-daily/cookies.json` 是否存在且包含 auth_token 和 ct0。

### Step 2: 读取 SKILL.md
打开 `~/.openclaw/workspace/skills/x-twitter-daily-v2/SKILL.md` 了解完整流程。

### Step 3: 执行数据抓取
```bash
cd ~/.openclaw/workspace/twitter-daily && python3 x_v2.py
```
如果没有报错，会输出到 `daily_raw.json`。

### Step 4: 筛选 Top 60 推文
用 Python 从 `daily_raw.json` 中提取推文，按点赞数降序取前 60 条，去掉 retweet 和空内容。

### Step 5: LLM 翻译 + 话题聚类
将 60 条推文发送给 LLM，同时做翻译和话题聚类。
- 翻译要求：准确、保留技术术语不翻译
- 聚类要求：5-12 个话题，含观点线

### Step 6: 写入 JSON
输出 4 个文件到 `/data/twitter-daily/latest/`：
- meta.json
- tweets.json（含 translated_text）
- topics.json（含 opinion_lines）
- timelines.json

### Step 7: 验证
```bash
python3 /data/twitter-daily/latest/validate.py
```

### Step 8: 归档
```bash
cp /data/twitter-daily/latest/*.json /data/twitter-daily/archive/$(date +%F)/
```

### Step 9: 整理简报
生成一段昨日简报文字，包含：
- 数据概览（xx条推文、xx个话题、xx位作者）
- 热门话题 Top 5（标题 + 一句话摘要）
- 访问网址：可在 `/data/twitter-daily/latest/` 查看

### Step 10: 睡眠 10 秒确保推送稳定
然后通过 announce 机制发消息。

## 注意事项
- Cookie 过期：如果 x_v2.py 报 login 错误，通知用户重新设置 Cookie
- 429 Rate Limit：间隔 3-5 秒抓取一个用户，首次抓取可能中限速，重试即可
- 如果某步骤失败，输出错误信息（不要静默失败）
