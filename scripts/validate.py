#!/usr/bin/env python3
import json, os, sys

DATA_DIR = "/data/twitter-daily/latest"

def load(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)

errors = []

# 1. JSON 合法性
for f in ["meta.json", "tweets.json", "topics.json", "timelines.json"]:
    try:
        load(f)
    except Exception as e:
        errors.append(f"JSON 解析失败: {f} — {e}")

if errors:
    print("❌ 验证失败:")
    for e in errors: print(f" - {e}")
    sys.exit(1)

meta = load("meta.json")
tweets = load("tweets.json")
topics_data = load("topics.json")
timelines_data = load("timelines.json")

tweet_ids = {t["id"] for t in tweets}
topic_ids = {t["id"] for t in topics_data["topics"]}

# 2. 数量一致性
if meta["stats"]["total_tweets"] != len(tweets):
    errors.append(f"total_tweets 不匹配: {meta['stats']['total_tweets']} vs {len(tweets)}")
if meta["stats"]["total_topics"] != len(topics_data["topics"]):
    errors.append(f"total_topics 不匹配: {meta['stats']['total_topics']} vs {len(topics_data['topics'])}")

# 3. ID 引用完整性
for topic in topics_data["topics"]:
    for tid in topic["tweets"]:
        if tid not in tweet_ids:
            errors.append(f"topic {topic['id']} 引用了不存在的 tweet: {tid}")
    for ol in topic.get("opinion_lines", []):
        for tid in ol["tweets"]:
            if tid not in tweet_ids:
                errors.append(f"topic {topic['id']} opinion_line 引用了不存在的 tweet: {tid}")

for tl in timelines_data.get("timelines", []):
    if tl["topic_id"] not in topic_ids:
        errors.append(f"timeline {tl['id']} 引用了不存在的 topic: {tl['topic_id']}")
    for ev in tl["events"]:
        if ev["tweet_id"] and ev["tweet_id"] not in tweet_ids:
            errors.append(f"timeline {tl['id']} event 引用了不存在的 tweet: {ev['tweet_id']}")

# 4. 唯一性
if len(tweet_ids) != len(tweets):
    errors.append("tweets.json 中存在重复 id")
if len(topic_ids) != len(topics_data["topics"]):
    errors.append("topics.json 中存在重复 id")

if errors:
    print("❌ 验证失败:")
    for e in errors: print(f" - {e}")
    sys.exit(1)
else:
    print(f"✅ 验证通过 — {len(tweets)} 条推文, {len(topics_data['topics'])} 个话题, {len(timelines_data.get('timelines', []))} 条时间线")
