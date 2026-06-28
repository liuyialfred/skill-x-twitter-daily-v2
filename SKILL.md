---
name: x-twitter-daily-v2
description: |
  X/Twitter 每日科技推文爬取 → 翻译 → 话题聚类 → 输出 JSON + 微信推送。
  每天 08:30 (UTC+8) 自动执行，产出 4 个 JSON 到 /data/twitter-daily/latest/。

  触发词：
  - 推特日报, Twitter Daily, X 日报
  - 科技推文, AI 新闻聚合
  - x-twitter-daily, twitter-daily-v2
  - 每日简报

  类型：Workflow — 每日定时工作流

secrets:
  - X auth_token / ct0 cookie（通过 set-cookie 存入 cookies.json）

dependencies:
  - python3 (requests, json 标准库)
  - ~/.openclaw/workspace/twitter-daily/x_v2.py（X 数据抓取核心）
  - OpenClaw cron（定时调度）
---

# x-twitter-daily-v2 — X/Twitter 日报工作流

## 概述

每天 08:30 (UTC+8) 自动执行：
1. 用 X 内部 GraphQL API 抓取追踪账号列表的最新推文
2. 筛选高互动推文（60 条，按点赞数排名）
3. 用 LLM 翻译为中文 + 话题聚类
4. 输出 4 个 JSON 到 `/data/twitter-daily/latest/`
5. 归档到 `/data/twitter-daily/archive/YYYY-MM-DD/`
6. 验证数据完整性
7. 通过微信推送昨日简报

## 前置条件

### 1. X 登录 Cookie

使用现有的 `~/.openclaw/workspace/twitter-daily/cookies.json`，包含 `auth_token` 和 `ct0`。

如需更新：
```bash
cd ~/.openclaw/workspace/twitter-daily
python3 x_v2.py --set-cookie
```

### 2. 账号列表

`~/.openclaw/workspace/twitter-daily/config.json` 中维护追踪用户列表。

### 3. 数据目录

```bash
mkdir -p /data/twitter-daily/{latest,archive,raw}
```

## 文件结构

```
~/.openclaw/workspace/skills/x-twitter-daily-v2/
├── SKILL.md
├── agents/
│   └── scheduler/
│       └── agent.md              # 定时任务 agent 配置
├── scripts/
│   └── daily_pipeline.sh         # 核心流水线脚本
```

## 每日工作流

```
08:30 启动
  │
  ├─ Step 1: 抓取（x_v2.py 拉取追踪账号近24h推文）
  │   输出: /data/twitter-daily/raw/YYYY-MM-DD.json
  │
  ├─ Step 2: 筛选（去重、去转推、按互动排名取Top 60）
  │   输出: /data/twitter-daily/latest/tweets.json
  │
  ├─ Step 3: LLM 翻译 + 话题聚类
  │   输出: topics.json（12话题 + 观点线）
  │         timelines.json（时间线）
  │         meta.json（元数据）
  │
  ├─ Step 4: 验证（validate.py）
  │
  ├─ Step 5: 归档（复制到 archive/YYYY-MM-DD/）
  │
  └─ Step 6: 微信推送（简报 + 网址）
```

## 输出文件

```
/data/twitter-daily/latest/
├── meta.json        # 元信息（日期、数量统计）
├── tweets.json      # 60条推文（含中英文）
├── topics.json      # 12个话题聚类
├── timelines.json   # 事件时间线
```

网站端：Next.js 直接读取 latest 目录，无需额外通知。

## 定时任务

OpenClaw cron 配置（自动生效）：

```json
{
  "name": "x-twitter-daily",
  "schedule": {"kind": "cron", "expr": "30 8 * * *", "tz": "Asia/Shanghai"},
  "sessionTarget": "isolated",
  "payload": {
    "kind": "agentTurn",
    "message": "执行 x-twitter-daily-v2 工作流"
  },
  "delivery": {
    "mode": "announce",
    "channel": "openclaw-weixin"
  }
}
```
