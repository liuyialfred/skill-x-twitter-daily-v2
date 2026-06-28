#!/usr/bin/env python3
"""
X Digest v2 — Fetch tweets via X's internal GraphQL API.
Based on X-to-Obsidian's approach. Requires login cookies.

Setup:
  1. Login to X in any browser
  2. Export cookies → paste auth_token & ct0
  3. python3 x_v2.py --add <username>     # Add tracked accounts
  4. python3 x_v2.py                       # Run digest

No Developer API key needed. No billing. No macOS dependency.
"""

import json
import os
import re
import sys
import time
import hashlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
COOKIES_PATH = os.path.join(SCRIPT_DIR, "cookies.json")

TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# ============================================================================
# HTTP helpers
# ============================================================================

def http_get(url, headers=None, timeout=30):
    if headers is None:
        headers = {}
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except Exception as e:
        return 0, str(e)


# ============================================================================
# Cookie management
# ============================================================================

def get_cookie_string():
    """Get cookie string from cookies.json or prompt user."""
    if os.path.exists(COOKIES_PATH):
        with open(COOKIES_PATH) as f:
            data = json.load(f)
        raw = data.get("cookie_string", "")
        if raw:
            return raw
        # Rebuild from fields
        auth = data.get("auth_token", "")
        ct0 = data.get("ct0", "")
        extra = data.get("extra", {})
        if auth and ct0:
            pairs = [f"auth_token={auth}", f"ct0={ct0}"]
            for k, v in extra.items():
                pairs.append(f"{k}={v}")
            return "; ".join(pairs)
    
    print("❌ No cookies found.")
    print("")
    print("How to get cookies:")
    print("  1. Open x.com in Chrome/Firefox and log in")
    print("  2. Press F12 → Application → Cookies → x.com")
    print("  3. Copy the VALUE of 'auth_token' and 'ct0'")
    print("")
    print("Then run:")
    print("  python3 x_v2.py --set-cookie")
    return None


def cmd_set_cookie():
    """Interactive cookie setup."""
    print("Paste your full Cookie string (from browser DevTools -> Network -> Request Headers -> Cookie):")
    print("Or paste just auth_token and ct0 values:")
    print("")
    
    auth = input("auth_token: ").strip()
    ct0 = input("ct0 (optional, press Enter to skip): ").strip()
    
    if auth:
        pairs = [f"auth_token={auth}"]
        if ct0:
            pairs.append(f"ct0={ct0}")
        cookie_str = "; ".join(pairs)
    else:
        cookie_str = input("Full Cookie string: ").strip()
    
    if not cookie_str:
        print("❌ No cookie provided.")
        return
    
    # Parse into parts
    data = {"cookie_string": cookie_str, "auth_token": auth, "ct0": ct0, "extra": {}}
    
    # Extract ct0 from cookie string if not provided
    if not data["ct0"]:
        m = re.search(r'ct0=([a-f0-9]+)', cookie_str)
        if m:
            data["ct0"] = m.group(1)
    
    # Extract auth_token
    if not data["auth_token"]:
        m = re.search(r'auth_token=([^;]+)', cookie_str)
        if m:
            data["auth_token"] = m.group(1)
    
    with open(COOKIES_PATH, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"✅ Cookies saved to {COOKIES_PATH}")
    print(f"   auth_token: {data['auth_token'][:20]}...")
    print(f"   ct0:        {data.get('ct0', 'N/A')[:20] if data.get('ct0') else 'N/A'}")


# ============================================================================
# Frontend extraction (like load_x_frontend in X-to-Obsidian)
# ============================================================================

def load_x_frontend(cookie_string):
    """Load X.com/home with cookies to extract bearer token and query IDs."""
    print("  🔑 Loading X frontend...")
    
    status, html = http_get(
        "https://x.com/home",
        headers={
            "Cookie": cookie_string,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    
    if status != 200:
        raise RuntimeError(f"X.com/home returned HTTP {status}. Cookie might be expired.")
    
    # Find main JS bundle
    scripts = re.findall(r'<script[^>]+src="([^"]+\.js)"', html)
    main_url = None
    for s in scripts:
        if "/main." in s:
            main_url = urllib.parse.urljoin("https://x.com", s)
            break
    
    if not main_url:
        raise RuntimeError("Could not find main JS bundle. X might have changed their architecture.")
    
    print(f"  📦 Main JS: {main_url}")
    
    _, main_js = http_get(main_url)
    
    # Extract bearer token
    bearer = re.search(r"Bearer ([A-Za-z0-9%._-]+)", main_js)
    if not bearer:
        # Try alternative patterns
        alt_patterns = [r'Bearer ([A-Za-z0-9%._-]{10,})']
        for p in alt_patterns:
            bearer = re.search(p, main_js)
            if bearer:
                break
    
    if not bearer:
        raise RuntimeError("Could not extract bearer token from JS bundle.")
    
    print(f"  🔐 Bearer: {bearer.group(1)[:40]}...")
    
    # Extract GraphQL operations
    def extract_operation(op_name):
        idx = main_js.find(f'operationName:"{op_name}"')
        if idx < 0:
            return None
        
        # Search for queryId nearby
        search_region = main_js[max(0, idx - 2000):idx + 500]
        chunk = main_js[max(0, idx - 500):idx + 500]
        
        query_id = re.search(r'queryId:"([^"]+)"', chunk)
        if not query_id:
            # Try broader search
            query_id = re.search(r'queryId:"([^"]+)"', search_region)
        
        if not query_id:
            return None
        
        # Extract feature switches from the broader region
        def arr(key):
            m = re.search(key + r":\[([^\]]*)\]", search_region)
            return re.findall(r'"([^"]+)"', m.group(1)) if m else []
        
        return {
            "queryId": query_id.group(1),
            "operationName": op_name,
            "featureSwitches": arr("featureSwitches"),
            "fieldToggles": arr("fieldToggles"),
        }
    
    ops = {}
    for name in ["UserByScreenName", "UserTweets"]:
        op = extract_operation(name)
        if op:
            print(f"  📋 {name} → queryId: {op['queryId']}")
            ops[name] = op
        else:
            print(f"  ⚠️  {name} not found - this might break")
    
    if not ops:
        raise RuntimeError("No GraphQL operations found. X might have changed.")
    
    return bearer.group(1), ops


# ============================================================================
# X GraphQL Client
# ============================================================================

class XClient:
    def __init__(self, cookie, ct0, bearer, operations):
        self.cookie = cookie
        self.ct0 = ct0
        self.bearer = bearer
        self.ops = operations
    
    def gql(self, op_name, variables, referer):
        if op_name not in self.ops:
            raise RuntimeError(f"Unknown operation: {op_name}")
        
        op = self.ops[op_name]
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
        }
        if op.get("featureSwitches"):
            params["features"] = json.dumps(
                {k: True for k in op["featureSwitches"]}, 
                separators=(",", ":")
            )
        if op.get("fieldToggles"):
            params["fieldToggles"] = json.dumps(
                {k: True for k in op["fieldToggles"]}, 
                separators=(",", ":")
            )
        
        url = (f"https://x.com/i/api/graphql/{op['queryId']}/{op_name}?" 
               + urllib.parse.urlencode(params))
        
        headers = {
            "Authorization": f"Bearer {self.bearer}",
            "Cookie": self.cookie,
            "X-Csrf-Token": self.ct0 or "",
            "x-twitter-client-language": "en",
            "Accept": "*/*",
            "Referer": referer,
            "User-Agent": USER_AGENT,
        }
        
        status, body = http_get(url, headers)
        
        if status >= 400:
            raise RuntimeError(f"GraphQL {op_name} error {status}: {body[:300]}")
        
        try:
            data = json.loads(body)
        except:
            raise RuntimeError(f"GraphQL {op_name} returned invalid JSON")
        
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        
        return data
    
    def user_id(self, handle):
        """Get user rest_id by screen name."""
        data = self.gql(
            "UserByScreenName",
            {"screen_name": handle},
            f"https://x.com/{handle}"
        )
        # Response can be under 'user' or 'user_result_by_screen_name'
        user_data = data.get("data", {})
        result = None
        for key in ["user", "user_result_by_screen_name"]:
            result = user_data.get(key, {}).get("result")
            if result and result.get("rest_id"):
                break
        if not result or not result.get("rest_id"):
            # Dump for debugging
            import sys
            json.dump(data, sys.stderr, indent=2)
            raise RuntimeError(f"Could not resolve user ID for @{handle}")
        return result["rest_id"], result.get("legacy", {})
    
    def user_tweets(self, user_id, handle, count=40, cursor=None):
        """Fetch user tweets with optional cursor for pagination."""
        variables = {
            "userId": user_id,
            "count": count,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": False,
        }
        if cursor:
            variables["cursor"] = cursor
        
        return self.gql("UserTweets", variables, f"https://x.com/{handle}")


# ============================================================================
# Tweet extraction (from X-to-Obsidian's unwrap_tweet / walk_tweets)
# ============================================================================

def unwrap_tweet(obj):
    """Unwrap nested tweet data from GraphQL response."""
    if not isinstance(obj, dict):
        return None
    if obj.get("__typename") == "Tweet" and obj.get("rest_id") and obj.get("legacy"):
        return obj
    for key in ("tweet", "tweet_results", "result"):
        child = obj.get(key)
        if isinstance(child, dict):
            found = unwrap_tweet(child)
            if found:
                return found
    return None


def walk_tweets(obj, out):
    """Recursively find all tweet objects."""
    if isinstance(obj, dict):
        tweet = unwrap_tweet(obj)
        if tweet:
            out.append(tweet)
        for value in obj.values():
            walk_tweets(value, out)
    elif isinstance(obj, list):
        for item in obj:
            walk_tweets(item, out)


def bottom_cursor(obj):
    """Find the bottom cursor for pagination."""
    if isinstance(obj, dict):
        if obj.get("cursorType") == "Bottom" and obj.get("value"):
            return obj["value"]
        for value in obj.values():
            found = bottom_cursor(value)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = bottom_cursor(item)
            if found:
                return found
    return None


def extract_tweet_info(tweet):
    """Extract readable info from a tweet object."""
    legacy = tweet.get("legacy", {})
    text = legacy.get("full_text", "")
    created = legacy.get("created_at", "")
    
    # Parse date
    parsed_date = None
    display_date = created
    try:
        parsed_date = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
        display_date = parsed_date.astimezone(TZ).strftime("%m-%d %H:%M")
    except:
        pass
    
    # Views
    views_raw = tweet.get("views", {}).get("count", "0")
    try:
        views = int(views_raw)
    except (TypeError, ValueError):
        views = 0
    
    # Metrics
    likes = legacy.get("favorite_count", 0)
    rts = legacy.get("retweet_count", 0)
    replies = legacy.get("reply_count", 0)
    quotes = legacy.get("quote_count", 0)
    
    # Author
    author_result = tweet.get("core", {}).get("user_results", {}).get("result", {})
    author = author_result.get("legacy", {}).get("screen_name", "")
    
    # Source
    source = legacy.get("source", "")
    source = re.sub(r'<[^>]+>', '', source)
    
    # Card
    card_url = ""
    card = legacy.get("card")
    if card:
        bindings = card.get("binding_values", [])
        if isinstance(bindings, list):
            for b in bindings:
                if isinstance(b, dict):
                    for v in b.values():
                        if isinstance(v, dict) and v.get("type") == "STRING":
                            val = v.get("string_value", "")
                            if val.startswith("http"):
                                card_url = val
    
    # Media
    media_list = legacy.get("extended_entities", {}).get("media", [])
    media_types = [m.get("type", "") for m in media_list]
    
    # Check if it's a retweet (check both text prefix and object structure)
    is_retweet = text.startswith("RT @") or bool(legacy.get("retweeted_status_result"))
    
    return {
        "id": tweet.get("rest_id", ""),
        "text": text[:500] if text else "",
        "created": display_date,
        "parsed_date_iso": parsed_date.isoformat() if parsed_date else None,
        "views": views,
        "likes": likes,
        "retweets": rts,
        "replies": replies,
        "quotes": quotes,
        "author": author,
        "source": source,
        "url": f"https://x.com/{author}/status/{tweet.get('rest_id', '')}" if author and tweet.get('rest_id') else "",
        "media": media_types,
        "card_url": card_url,
        "is_retweet": is_retweet,
    }


# ============================================================================
# Digest generation
# ============================================================================

def generate_digest(all_tweets, days=1, skip_retweets=True):
    """Collect raw tweets into structured data for LLM processing."""
    cutoff = datetime.now(TZ) - timedelta(days=days)
    
    collected = []
    for user_info, tweets in all_tweets:
        name = user_info.get("name", "")
        handle = user_info.get("username", "")
        seen_ids = set()
        for t in tweets:
            tid = t.get("id", "")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            if skip_retweets and t.get("is_retweet"):
                continue
            if t.get("parsed_date_iso"):
                try:
                    pd = datetime.fromisoformat(t["parsed_date_iso"])
                    if pd < cutoff:
                        continue
                except: pass
            txt = t.get("text", "")
            if txt.startswith("@") and len(txt.split()[0]) < 50:
                continue
            # Filter Elon's non-tech content
            if handle == "elonmusk":
                ai_signals = ["ai", "grok", "xai", "spacex", "tesla", "robot", "model", 
                             "chip", "compute", "engineering", "starship", "rocket",
                             "openai", "neuralink", "autonomous", "software", "code"]
                if not any(s in txt.lower() for s in ai_signals):
                    continue
            collected.append({
                "author": name,
                "handle": handle,
                "text": re.sub(r'https://t\\.co/\\w+', '', txt).strip()[:400],
                "url": t.get("url", ""),
                "date": t.get("created", ""),
                "likes": t.get("likes", 0),
                "retweets": t.get("retweets", 0),
                "replies": t.get("replies", 0),
                "views": t.get("views", 0),
                "media": t.get("media", []),
            })
    
    return collected  # return raw list, not formatted text


# ============================================================================
# Config management
# ============================================================================

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"users": []}
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ============================================================================
# Commands
# ============================================================================

def cmd_add_user(username):
    cfg = load_config()
    username = username.lstrip("@")
    
    for u in cfg["users"]:
        if u["username"].lower() == username.lower():
            print(f"  ⚠️ @{username} already tracked.")
            return
    
    cfg["users"].append({
        "id": "",
        "name": username,
        "username": username,
    })
    save_config(cfg)
    print(f"  ✅ Added @{username}")


def cmd_list_users():
    cfg = load_config()
    if not cfg["users"]:
        print("  📭 No users tracked.")
        return
    print(f"  📋 Tracked users ({len(cfg['users'])}):")
    for u in cfg["users"]:
        print(f"    @{u['username']}  ({u.get('name', '')})")


def cmd_remove_user(username):
    cfg = load_config()
    target = username.lstrip("@").lower()
    before = len(cfg["users"])
    cfg["users"] = [u for u in cfg["users"] if u["username"].lower() != target]
    if len(cfg["users"]) < before:
        save_config(cfg)
        print(f"  ✅ Removed @{target}")
    else:
        print(f"  ⚠️ @{target} not found")


def cmd_fetch(days=1, test_mode=False):
    """Main fetch command."""
    cookie = get_cookie_string()
    if not cookie:
        print("Run: python3 x_v2.py --set-cookie first")
        return
    
    cfg = load_config()
    if not cfg["users"]:
        print("❌ No users configured. Run: python3 x_v2.py --add <username>")
        return
    
    # Parse ct0 from cookie
    ct0 = ""
    m = re.search(r'ct0=([a-f0-9]+)', cookie)
    if m:
        ct0 = m.group(1)
    
    try:
        # Step 1: Load X frontend
        bearer, ops = load_x_frontend(cookie)
        
        # Step 2: Create client
        client = XClient(cookie, ct0, bearer, ops)
        
        # Step 3: Fetch tweets for each user
        all_tweets = []
        for u in cfg["users"]:
            handle = u["username"]
            print(f"  📡 @{handle}...", end=" ", flush=True)
            
            try:
                # Get user ID
                uid, profile = client.user_id(handle)
                
                # Update config with real name
                if profile.get("name") and u.get("name") == handle:
                    u["name"] = profile["name"]
                    u["description"] = (profile.get("description") or "")[:100]
                
                # Fetch tweets
                data = client.user_tweets(uid, handle, count=20)
                tweets = []
                walk_tweets(data, tweets)
                
                # Deduplicate, filter retweets
                seen_ids = set()
                deduped = []
                for t in tweets:
                    tid = t.get("rest_id", "")
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    deduped.append(t)
                
                extracted = [extract_tweet_info(t) for t in deduped]
                all_tweets.append((u, extracted))
                
                print(f"{len(extracted)} tweets")
                
            except Exception as e:
                print(f"❌ {e}")
                all_tweets.append((u, []))
            
            time.sleep(5)
        
        save_config(cfg)  # Save updated names
        
        # Step 4: Collect tweets for LLM processing
        digest = generate_digest(all_tweets, days=days)
        today_str = datetime.now(TZ).strftime("%Y-%m-%d")
        
        digest_data = {
            "fetched_at": datetime.now(TZ).isoformat(),
            "date": today_str,
            "total_tweets": len(digest),
            "authors": list(set(t["handle"] for t in digest)),
            "tweets": digest
        }
        
        # Save for agent consumption
        digest_path = os.path.join(SCRIPT_DIR, "daily_raw.json")
        with open(digest_path, "w") as f:
            json.dump(digest_data, f, indent=2, ensure_ascii=False)
        
        if test_mode:
            print(f"\n✅ {len(digest)} tweets from {len(digest_data['authors'])} authors → {digest_path}")
        else:
            print(f"✅ {len(digest)} tweets → {digest_path}")
    
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nPossible fixes:")
        print("  1. Cookie expired → re-login and update cookie")
        print("  2. X changed architecture → script needs update")
        raise


# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) < 2:
        cmd_fetch()
        return
    
    cmd = sys.argv[1]
    
    if cmd == "--set-cookie":
        cmd_set_cookie()
    elif cmd == "--add" and len(sys.argv) >= 3:
        cmd_add_user(sys.argv[2])
    elif cmd == "--list":
        cmd_list_users()
    elif cmd == "--remove" and len(sys.argv) >= 3:
        cmd_remove_user(sys.argv[2])
    elif cmd == "--test":
        cookie = get_cookie_string()
        if cookie:
            cmd_fetch(days=7, test_mode=True)
    elif cmd == "--help" or cmd == "-h":
        print(__doc__)
        print("Commands:")
        print("  python3 x_v2.py                         # Fetch digest")
        print("  python3 x_v2.py --set-cookie            # Set login cookie")
        print("  python3 x_v2.py --add <username>        # Add tracked user")
        print("  python3 x_v2.py --list                  # List tracked users")
        print("  python3 x_v2.py --remove <username>     # Remove user")
        print("  python3 x_v2.py --test                  # Test with 7-day window")
    else:
        print(f"Unknown command: {cmd}")
        print("Use --help for usage")


if __name__ == "__main__":
    main()
