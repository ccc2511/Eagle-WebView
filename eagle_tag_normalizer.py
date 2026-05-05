"""
Eagle タグ正規化スクリプト
PC側で実行。スマホのブラウザから /normalize-tags エンドポイントでトリガー可能。
単体実行: python eagle_tag_normalizer.py
"""
import urllib.request
import urllib.parse
import json
import re
import sys

EAGLE_PORT = 41595
BASE = f"http://localhost:{EAGLE_PORT}"

def api_get(path, params=None):
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = BASE + path + qs
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())

def api_post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def normalize_tag(tag):
    t = tag
    for ch in "{}[]()":
        t = t.replace(ch, "")
    t = re.sub(r" +", " ", t).strip()
    return t

def normalize_tags(tags):
    seen = set()
    result = []
    for tag in tags:
        n = normalize_tag(tag)
        if not n:
            continue
        key = n.lower()
        if key not in seen:
            seen.add(key)
            result.append(n)
    return result

def tags_changed(a, b):
    if len(a) != len(b):
        return True
    return any(x != y for x, y in zip(a, b))

def run(dry_run=True, log=print, stop_flag=None):
    log("=" * 50)
    log(f"Eagle タグ正規化 ({'プレビュー' if dry_run else '実行'}モード)")
    log("=" * 50)

    # Step1: 全タグ取得
    log("\n[1] /api/tag/list から全タグ取得中...")
    data = api_get("/api/tag/list")
    all_tags = [t if isinstance(t, str) else t.get("name", "") for t in (data.get("data") or [])]
    all_tags = [t for t in all_tags if t]
    log(f"    全タグ数: {len(all_tags)}件")

    # Step2: 正規化が必要なタグを抽出
    dirty_tag_map = {}
    for tag in all_tags:
        n = normalize_tag(tag)
        if n and tag != n:
            dirty_tag_map[tag] = n

    log(f"\n[2] 正規化が必要なタグ: {len(dirty_tag_map)}件")
    for old, new in list(dirty_tag_map.items())[:10]:
        log(f"    \"{old}\" → \"{new}\"")
    if len(dirty_tag_map) > 10:
        log(f"    ...他{len(dirty_tag_map)-10}件")

    if not dirty_tag_map:
        log("\n正規化が必要なタグはありませんでした。")
        return {"status": "ok", "message": "No tags need normalization"}

    # Step3: 各汚染タグを複数orderByで取得して重複排除（200件制限を回避）
    log(f"\n[3] 汚染タグのアイテムを検索中...")
    change_map = {}
    orders = ["-CREATEDATE", "CREATEDATE", "-MODIFICATIONTIME", "MODIFICATIONTIME",
              "NAME", "-NAME", "-FILESIZE", "FILESIZE", "-RESOLUTION", "RESOLUTION"]
    for i, (old_tag, new_tag) in enumerate(dirty_tag_map.items()):
        if stop_flag and stop_flag():
            log("⏹ 停止しました")
            return {"status": "stopped"}
        tag_items = {}
        for order in orders:
            res = api_get("/api/item/list", {"limit": 200, "tags": old_tag, "orderBy": order})
            for item in (res.get("data") or []):
                tag_items[item["id"]] = item
        log(f"    [{i+1}/{len(dirty_tag_map)}] \"{old_tag[:40]}\": {len(tag_items)}件")
        for item in tag_items.values():
            old_tags = item.get("tags") or []
            new_tags = normalize_tags(old_tags)
            if tags_changed(old_tags, new_tags):
                change_map[item["id"]] = {
                    "id": item["id"],
                    "name": item.get("name", ""),
                    "old_tags": old_tags,
                    "new_tags": new_tags,
                }
    log(f"    完了: {len(change_map)}件変更あり")

    log(f"\n[4] 変更対象アイテム: {len(change_map)}件")

    if dry_run:
        log("\n--- プレビュー（上位20件）---")
        for item in list(change_map.values())[:20]:
            log(f"  {item['name'][:50]}")
            removed = set(item['old_tags']) - set(item['new_tags'])
            added = set(item['new_tags']) - set(item['old_tags'])
            if removed: log(f"    削除: {list(removed)[:5]}")
            if added:   log(f"    追加: {list(added)[:5]}")
        log("\n--dry_run=False で実行すると実際に更新されます--")
        return {"status": "preview", "count": len(change_map)}

    # Step4: 実際に更新
    log(f"\n[5] 更新中...")
    done = 0
    errors = 0
    for i, item in enumerate(change_map.values()):
        if stop_flag and stop_flag():
            log(f"⏹ 停止しました（{done}件更新済み）")
            return {"status": "stopped", "updated": done, "errors": errors}
        sys.stdout.write(f"\r    {i+1}/{len(change_map)}件処理中...")
        sys.stdout.flush()
        try:
            res = api_post("/api/item/update", {"id": item["id"], "tags": item["new_tags"]})
            if res.get("status") == "success":
                done += 1
            else:
                errors += 1
                log(f"\n    NG: {item['name'][:40]} - {res.get('message')}")
        except Exception as e:
            errors += 1
            log(f"\n    エラー: {item['name'][:40]} - {e}")
    print()

    log(f"\n完了! 更新: {done}件 / エラー: {errors}件")
    return {"status": "done", "updated": done, "errors": errors}

if __name__ == "__main__":
    # コマンドライン引数で --apply を付けると実際に更新
    dry = "--apply" not in sys.argv
    if not dry:
        ans = input("本当に実行しますか？ (yes/no): ")
        if ans.strip().lower() != "yes":
            print("キャンセルしました")
            sys.exit(0)
    run(dry_run=dry)
